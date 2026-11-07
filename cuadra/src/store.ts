import { create } from 'zustand'
import { formatCount } from './domain/money'
import type { Decision } from './domain/types'
import { createBrowserDb } from './data/browserDb'
import { loadCase, type CaseData } from './data/caseLoader'
import type { Db } from './data/db'
import {
  clearDecision,
  confirmBulk,
  importDecisions,
  loadDecisionsIntoDb,
  replayLog,
  undoBulk,
  writeDecision,
  type LogEntry,
} from './data/decisions'
import type { ExportOptions } from './data/exportSubmission'
import { ingest, type DatasetMeta, type Progress } from './data/ingest'
import { appendLog, readLog } from './data/log'
import { datasetKey, type Plan, type SourceFile } from './data/preflight'
import { buildQueue, DEFAULT_FILTERS, queueWindow, type QueueFilters, type QueueStep } from './data/queue'

export type Screen = 'panel' | 'queue' | 'bench' | 'export'

type UndoEntry =
  | { kind: 'one'; rid: number; pos: number | null; txnId: string; before: Decision | null }
  | { kind: 'batch'; batch: string; count: number; rids: number[] }

type Notice = { tone: 'ok' | 'error'; text: string; id: number }

type State = {
  db: Db | null
  meta: DatasetMeta | null
  key: string
  pdfFiles: SourceFile[]
  progress: Progress | null
  loadError: string | null
  screen: Screen
  filters: QueueFilters
  queueTotal: number
  queueVersion: number
  cursorPos: number
  benchRid: number | null
  benchPos: number | null
  resolved: Set<number>
  usedItems: Map<string, string>
  undo: UndoEntry[]
  revision: number
  notice: Notice | null
  helpOpen: boolean
  exportOptions: ExportOptions
  sessionTimes: number[]
}

const WINDOW = 40
const PREFETCH = 4
const CACHE_LIMIT = 60

const cases = new Map<number, Promise<CaseData>>()
let queueAhead: QueueStep[] = []
let writeChain: Promise<unknown> = Promise.resolve()

type Actions = {
  loadDataset(plan: Plan): Promise<void>
  go(screen: Screen): void
  setFilters(filters: Partial<QueueFilters>): Promise<void>
  refreshQueue(): Promise<void>
  setCursor(pos: number): void
  openCase(rid: number, pos: number | null): void
  caseFor(rid: number): Promise<CaseData>
  step(direction: 1 | -1): Promise<void>
  decide(rid: number, decision: Decision): void
  undoLast(): Promise<void>
  bulkConfirm(minConf: number): Promise<number>
  importReview(text: string): Promise<string>
  notify(tone: Notice['tone'], text: string): void
  setHelp(open: boolean): void
  flushWrites(): Promise<unknown>
  setExportOptions(options: Partial<ExportOptions>): void
}

let noticeId = 0


export const useStore = create<State & Actions>((set, get) => {
  /** Writes go in order and in the background, so Enter never waits for storage. */
  const enqueueWrite = (work: () => Promise<unknown>) => {
    writeChain = writeChain.then(work).catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error)
      get().notify('error', `No se pudo guardar la última decisión: ${message}`)
    })
    return writeChain
  }

  const prefetch = (fromPos: number) => {
    const { db, meta, resolved } = get()
    if (!db || !meta) return
    queueAhead
      .filter((s) => s.pos > fromPos && !resolved.has(s.rid))
      .slice(0, PREFETCH)
      .forEach((s) => void cacheCase(s.rid))
  }

  const cacheCase = (rid: number) => {
    const { db, meta } = get()
    if (!db || !meta) return Promise.reject(new Error('Sin datos cargados'))
    const existing = cases.get(rid)
    if (existing) return existing
    const promise = loadCase(db, meta, rid, () => (get().benchRid === rid ? 'high' : 'low'))
    promise.catch(() => cases.delete(rid))
    cases.set(rid, promise)
    if (cases.size > CACHE_LIMIT) {
      const oldest = cases.keys().next().value
      if (oldest !== undefined) cases.delete(oldest)
    }
    return promise
  }

  const refreshWindow = async (pos: number) => {
    const { db } = get()
    if (!db) return
    queueAhead = await queueWindow(db, Math.max(1, pos - 4), WINDOW)
    prefetch(pos)
  }

  return {
    db: null,
    meta: null,
    key: '',
    pdfFiles: [],
    progress: null,
    loadError: null,
    screen: 'panel',
    filters: DEFAULT_FILTERS,
    queueTotal: 0,
    queueVersion: 0,
    cursorPos: 1,
    benchRid: null,
    benchPos: null,
    resolved: new Set(),
    usedItems: new Map(),
    undo: [],
    revision: 0,
    notice: null,
    helpOpen: false,
    exportOptions: { includeProposals: true, minConfidence: 0 },
    sessionTimes: [],

    loadDataset: async (plan) => {
      set({ progress: { step: 'Arrancando el motor de datos', done: 0, total: 7 }, loadError: null })
      try {
        const db = get().db ?? (await createBrowserDb())
        const meta = await ingest(db, plan, (progress) => set({ progress }))
        const key = datasetKey(plan)
        const log = await readLog(key)
        const decisions = replayLog(log)
        await loadDecisionsIntoDb(db, decisions)
        cases.clear()
        queueAhead = []
        const queueTotal = await buildQueue(db, DEFAULT_FILTERS)
        set({
          db,
          meta,
          key,
          pdfFiles: plan.pdfs,
          progress: null,
          screen: 'panel',
          filters: DEFAULT_FILTERS,
          queueTotal,
          cursorPos: 1,
          benchRid: null,
          benchPos: null,
          resolved: new Set(),
          usedItems: new Map(),
          undo: [],
          revision: 1,
          queueVersion: 1,
        })
        await refreshWindow(1)
        if (decisions.length) {
          get().notify('ok', `Revisión recuperada: ${formatCount(decisions.length)} movimientos ya decididos.`)
        }
      } catch (error) {
        set({
          progress: null,
          loadError: error instanceof Error ? error.message : String(error),
        })
      }
    },

    go: (screen) => {
      set({ screen })
      if (screen === 'bench' && get().benchRid === null) {
        void get().step(1)
      }
    },

    setFilters: async (filters) => {
      set({ filters: { ...get().filters, ...filters } })
      await get().refreshQueue()
    },

    refreshQueue: async () => {
      const { db, filters, queueVersion } = get()
      if (!db) return
      await writeChain
      const queueTotal = await buildQueue(db, filters)
      queueAhead = []
      set({ queueTotal, cursorPos: 1, queueVersion: queueVersion + 1, resolved: new Set() })
      await refreshWindow(1)
    },

    setCursor: (pos) => set({ cursorPos: pos }),

    openCase: (rid, pos) => {
      set({ benchRid: rid, benchPos: pos, screen: 'bench', ...(pos ? { cursorPos: pos } : {}) })
      void cacheCase(rid)
      if (pos) void refreshWindow(pos)
    },

    caseFor: (rid) => cacheCase(rid),

    step: async (direction) => {
      const { db, benchPos, resolved, queueTotal } = get()
      if (!db) return
      const current = benchPos ?? 0
      const local = queueAhead.filter((s) => !resolved.has(s.rid))
      let next =
        direction === 1
          ? local.find((s) => s.pos > current)
          : [...local].reverse().find((s) => s.pos < current)
      if (!next && (direction === 1 ? current < queueTotal : current > 1)) {
        const from = direction === 1 ? current + 1 : Math.max(1, current - WINDOW)
        const window = await queueWindow(db, from, WINDOW)
        queueAhead = window
        const fresh = window.filter((s) => !get().resolved.has(s.rid))
        next = direction === 1 ? fresh[0] : fresh.at(-1)
      }
      if (!next) {
        set({ benchRid: null, benchPos: null })
        get().notify('ok', direction === 1 ? 'Has llegado al final de la cola.' : 'Estás en el primer movimiento de la cola.')
        return
      }
      set({ benchRid: next.rid, benchPos: next.pos, cursorPos: next.pos })
      void cacheCase(next.rid)
      const remaining = queueAhead.filter((s) => s.pos > next.pos).length
      if (remaining < PREFETCH + 1) void refreshWindow(next.pos)
      else prefetch(next.pos)
    },

    decide: (rid, decision) => {
      const { db, key, resolved, usedItems, benchPos } = get()
      if (!db) return
      const before = cases.get(rid)
      const nextUsed = new Map(usedItems)
      decision.allocations.forEach((a) => nextUsed.set(a.itemId, decision.txnId))
      set({
        resolved: new Set(resolved).add(rid),
        usedItems: nextUsed,
        sessionTimes: [...get().sessionTimes.slice(-200), decision.at],
      })
      cases.delete(rid)
      void enqueueWrite(async () => {
        const previous = before ? (await before).decision : null
        set({
          undo: [
            ...get().undo.slice(-99),
            { kind: 'one', rid, pos: benchPos, txnId: decision.txnId, before: previous },
          ],
        })
        await writeDecision(db, rid, decision)
        await appendLog(key, { kind: 'set', decision })
        // Only now the database has it: views that read from it must refresh after this point.
        set({ revision: get().revision + 1 })
      })
    },

    undoLast: async () => {
      const { db, key, resolved, usedItems, queueVersion } = get()
      if (!db) return
      await writeChain
      const last = get().undo.at(-1)
      if (!last) {
        get().notify('ok', 'No hay nada que deshacer.')
        return
      }
      set({ undo: get().undo.slice(0, -1) })
      if (last.kind === 'batch') {
        await undoBulk(db, last.batch)
        await appendLog(key, { kind: 'unbulk', batch: last.batch })
        last.rids.forEach((rid) => cases.delete(rid))
        await get().refreshQueue()
        get().notify('ok', `Lote deshecho: ${last.count} movimientos vuelven a la cola.`)
        return
      }
      const nextResolved = new Set(resolved)
      nextResolved.delete(last.rid)
      const nextUsed = new Map([...usedItems].filter(([, txnId]) => txnId !== last.txnId))
      cases.delete(last.rid)
      let entry: LogEntry
      if (last.before) {
        await writeDecision(db, last.rid, last.before)
        entry = { kind: 'set', decision: last.before }
      } else {
        await clearDecision(db, last.rid)
        entry = { kind: 'clear', txnId: last.txnId }
      }
      await appendLog(key, entry)
      set({
        resolved: nextResolved,
        usedItems: nextUsed,
        benchRid: last.rid,
        benchPos: last.pos,
        screen: 'bench',
        revision: get().revision + 1,
        queueVersion: queueVersion + 1,
      })
    },

    bulkConfirm: async (minConf) => {
      const { db, key } = get()
      if (!db) return 0
      await writeChain
      const result = await confirmBulk(db, minConf)
      if (!result.count) return 0
      await appendLog(key, { kind: 'bulk', batch: result.batch, csv: result.csv, source: 'bulk', at: Date.now() })
      result.rids.forEach((rid) => cases.delete(rid))
      set({
        undo: [...get().undo.slice(-99), { kind: 'batch', batch: result.batch, count: result.count, rids: result.rids }],
        revision: get().revision + 1,
      })
      await get().refreshQueue()
      return result.count
    },

    importReview: async (text) => {
      const { db, key } = get()
      if (!db) return ''
      await writeChain
      const result = await importDecisions(db, text)
      if (result.applied) {
        await appendLog(key, { kind: 'bulk', batch: result.batch, csv: result.csv, source: 'imported', at: Date.now() })
      }
      cases.clear()
      set({ revision: get().revision + 1 })
      await get().refreshQueue()
      return `Importados ${result.applied}. Ya decididos aquí, sin cambios: ${result.skipped}. Movimientos desconocidos: ${result.unknown}.`
    },

    notify: (tone, text) => {
      noticeId += 1
      const id = noticeId
      set({ notice: { tone, text, id } })
      if (tone === 'ok') {
        setTimeout(() => {
          if (get().notice?.id === id) set({ notice: null })
        }, 3500)
      }
    },

    setHelp: (helpOpen) => set({ helpOpen }),
    flushWrites: () => writeChain,
    setExportOptions: (options) => set({ exportOptions: { ...get().exportOptions, ...options } }),
  }
})
