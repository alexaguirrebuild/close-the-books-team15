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
import { buildDiagnosis } from './data/diagnosis'
import { buildExportRows, type ExportOptions } from './data/exportSubmission'
import { ingest, type DatasetMeta, type Progress } from './data/ingest'
import { appendLog, readLog } from './data/log'
import { datasetKey, type Plan, type SourceFile } from './data/preflight'
import { buildQueue, DEFAULT_FILTERS, queueWindow, viewCounts, type QueueFilters, type QueueStep, type ViewCounts } from './data/queue'
import type { Draft } from './domain/draft'

export type Screen = 'panel' | 'review' | 'export'

type UndoEntry =
  | { kind: 'one'; rid: number; pos: number | null; txnId: string; before: Decision | null }
  | { kind: 'batch'; batch: string; count: number; rids: number[] }

type Notice = { tone: 'ok' | 'error'; text: string; id: number; undoable: boolean }

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
  bulkOpen: boolean
  listHidden: boolean
  counts: ViewCounts | null
  diagnosisRevision: number
  /** Goes up when cached movements can no longer be trusted, so a movement on screen reloads. */
  caseVersion: number
  exportOptions: ExportOptions
  sessionTimes: number[]
}

const NOTICE_MS = 3500
const NOTICE_UNDO_MS = 6000
const WINDOW = 40
const PREFETCH = 4
const CACHE_LIMIT = 60

const cases = new Map<number, Promise<CaseData>>()
/** Work in progress on a movement survives moving away from it, until it is saved or undone. */
const drafts = new Map<number, Draft>()
let queueAhead: QueueStep[] = []
let writeChain: Promise<unknown> = Promise.resolve()
/** The queue is one shared table: rebuilds go one at a time, and only the latest request counts. */
let queueChain: Promise<unknown> = Promise.resolve()
let queueRun = 0
/** A decision and the move to the next movement are one step: a second key press must not land in between. */
let advancing = false

type Actions = {
  loadDataset(plan: Plan): Promise<void>
  go(screen: Screen): void
  setFilters(filters: Partial<QueueFilters>): Promise<void>
  refreshQueue(): Promise<void>
  openCase(rid: number, pos: number | null): void
  jumpTo(pos: number): Promise<void>
  caseFor(rid: number): Promise<CaseData>
  step(direction: 1 | -1): Promise<void>
  decide(rid: number, decision: Decision): void
  decideAndNext(rid: number, decision: Decision): Promise<void>
  undoLast(): Promise<void>
  bulkConfirm(minConf: number): Promise<number>
  importReview(text: string): Promise<string>
  notify(tone: Notice['tone'], text: string, undoable?: boolean): void
  dismissNotice(): void
  setHelp(open: boolean): void
  setBulkOpen(open: boolean): void
  toggleList(): void
  refreshCounts(): Promise<void>
  /** Compares what would be exported with the golden. Costly on TRAIN, so it runs on demand. */
  refreshDiagnosis(): Promise<void>
  draftFor(rid: number): Draft | null
  rememberDraft(rid: number, draft: Draft): void
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
    const { db, queueVersion } = get()
    if (!db) return
    const window = await queueWindow(db, Math.max(1, pos - 4), WINDOW)
    if (get().queueVersion !== queueVersion) return
    queueAhead = window
    prefetch(pos)
  }

  const forgetCases = (rids: number[]) => {
    cases.clear()
    rids.forEach((rid) => drafts.delete(rid))
    set((s) => ({ caseVersion: s.caseVersion + 1 }))
  }

  const rebuildQueue = async (run: number) => {
    await writeChain
    const { db, filters } = get()
    if (!db || run !== queueRun) return
    // What was decided up to here is already out of the new queue; what arrives during the rebuild is not.
    const settled = new Set(get().resolved)
    const queueTotal = await buildQueue(db, filters)
    if (run !== queueRun) return
    const window = await queueWindow(db, 1, WINDOW)
    if (run !== queueRun) return
    queueAhead = window
    const resolved = new Set([...get().resolved].filter((rid) => !settled.has(rid)))
    // All at once: the list and the case change together, with nothing empty in between.
    const first = get().screen === 'review' ? (window.find((s) => !resolved.has(s.rid)) ?? null) : null
    set((s) => ({
      queueTotal,
      cursorPos: first?.pos ?? 1,
      queueVersion: s.queueVersion + 1,
      resolved,
      benchRid: first?.rid ?? null,
      benchPos: first?.pos ?? null,
    }))
    prefetch(first?.pos ?? 1)
    if (first) void cacheCase(first.rid)
    await get().refreshCounts()
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
    bulkOpen: false,
    listHidden: false,
    counts: null,
    diagnosisRevision: 0,
    caseVersion: 0,
    exportOptions: { includeProposals: true, minConfidence: 0 },
    sessionTimes: [],

    loadDataset: async (plan) => {
      if (get().progress) return
      set({ progress: { step: 'Arrancando el motor de datos', done: 0, total: 7 }, loadError: null })
      try {
        const db = get().db ?? (await createBrowserDb())
        const meta = await ingest(db, plan, (progress) => set({ progress }))
        const key = datasetKey(plan)
        const log = await readLog(key)
        const decisions = replayLog(log)
        await loadDecisionsIntoDb(db, decisions)
        cases.clear()
        drafts.clear()
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
        await get().refreshDiagnosis()
        await get().refreshCounts()
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
      if (screen === 'review' && get().benchRid === null) void get().step(1)
    },

    setFilters: async (filters) => {
      const next = { ...get().filters, ...filters }
      set({ filters: next })
      if (next.view === 'errors' && get().diagnosisRevision !== get().revision) await get().refreshDiagnosis()
      await get().refreshQueue()
    },

    refreshQueue: () => {
      queueRun += 1
      const run = queueRun
      queueChain = queueChain
        .then(() => rebuildQueue(run))
        .catch((error: unknown) => {
          get().notify('error', `No se pudo actualizar la lista: ${error instanceof Error ? error.message : String(error)}`)
        })
      return queueChain as Promise<void>
    },

    refreshDiagnosis: async () => {
      const { db, meta, exportOptions } = get()
      if (!db || !meta?.hasGolden) return
      await writeChain
      await buildExportRows(db, exportOptions)
      await buildDiagnosis(db)
      set({ diagnosisRevision: get().revision })
    },

    refreshCounts: async () => {
      const { db, filters, meta } = get()
      if (!db) return
      await writeChain
      set({ counts: await viewCounts(db, filters, Boolean(meta?.hasGolden)) })
    },

    openCase: (rid, pos) => {
      set({ benchRid: rid, benchPos: pos, screen: 'review', ...(pos ? { cursorPos: pos } : {}) })
      void cacheCase(rid)
      if (pos) void refreshWindow(pos)
    },

    jumpTo: async (pos) => {
      const { db, queueTotal } = get()
      if (!db || !queueTotal) return
      const [target] = await queueWindow(db, Math.min(Math.max(pos, 1), queueTotal), 1)
      if (target) get().openCase(target.rid, target.pos)
    },

    caseFor: (rid) => cacheCase(rid),

    step: async (direction) => {
      const { db, benchPos, benchRid, resolved, queueTotal, queueVersion } = get()
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
        // The queue or the movement changed while waiting: this answer belongs to the past.
        if (get().queueVersion !== queueVersion || get().benchRid !== benchRid) return
        queueAhead = window
        const fresh = window.filter((s) => !get().resolved.has(s.rid))
        next = direction === 1 ? fresh[0] : fresh.at(-1)
      }
      if (!next) {
        if (direction === 1) set({ benchRid: null, benchPos: null })
        else get().notify('ok', 'Estás en el primer movimiento de esta vista.')
        return
      }
      const target = next
      set({ benchRid: target.rid, benchPos: target.pos, cursorPos: target.pos })
      void cacheCase(target.rid)
      const remaining = queueAhead.filter((s) => s.pos > target.pos).length
      if (remaining < PREFETCH + 1) void refreshWindow(target.pos)
      else prefetch(target.pos)
    },

    decide: (rid, decision) => {
      const { db, key, benchPos } = get()
      if (!db) return
      const before = cases.get(rid)
      const draft = drafts.get(rid)
      set((s) => {
        const usedItems = new Map(s.usedItems)
        decision.allocations.forEach((a) => usedItems.set(a.itemId, decision.txnId))
        return {
          resolved: new Set(s.resolved).add(rid),
          usedItems,
          sessionTimes: [...s.sessionTimes.slice(-200), decision.at],
        }
      })
      cases.delete(rid)
      drafts.delete(rid)
      get().notify('ok', `${decision.source === 'confirmed' ? 'Confirmado' : 'Guardado'} ${decision.txnId}.`, true)
      void enqueueWrite(async () => {
        const previous = before ? ((await before.catch(() => null))?.decision ?? null) : null
        try {
          await writeDecision(db, rid, decision)
          await appendLog(key, { kind: 'set', decision })
        } catch (error) {
          // It was not saved: the movement goes back to pending, or the export would silently ignore the decision.
          await (previous ? writeDecision(db, rid, previous) : clearDecision(db, rid)).catch(() => undefined)
          if (draft) drafts.set(rid, draft)
          set((s) => {
            const resolved = new Set(s.resolved)
            resolved.delete(rid)
            return {
              resolved,
              usedItems: new Map([...s.usedItems].filter(([, txnId]) => txnId !== decision.txnId)),
              queueVersion: s.queueVersion + 1,
            }
          })
          throw error
        }
        // Only now the database has it: views that read from it must refresh after this point.
        set((s) => ({
          undo: [...s.undo.slice(-99), { kind: 'one', rid, pos: benchPos, txnId: decision.txnId, before: previous }],
          revision: s.revision + 1,
        }))
        void get().refreshCounts()
      })
    },

    decideAndNext: async (rid, decision) => {
      if (advancing) return
      advancing = true
      try {
        get().decide(rid, decision)
        await get().step(1)
      } finally {
        advancing = false
      }
    },

    undoLast: async () => {
      const { db, key } = get()
      if (!db) return
      const undoneBatches: number[] = []
      // In the same line as the writes: two quick undos, or an undo right after a decision, stay in order.
      await enqueueWrite(async () => {
        const last = get().undo.at(-1)
        if (!last) {
          get().notify('ok', 'No hay nada que deshacer.')
          return
        }
        set((s) => ({ undo: s.undo.slice(0, -1) }))
        if (last.kind === 'batch') {
          await undoBulk(db, last.batch)
          await appendLog(key, { kind: 'unbulk', batch: last.batch })
          forgetCases(last.rids)
          set((s) => ({ revision: s.revision + 1 }))
          undoneBatches.push(last.count)
          return
        }
        let entry: LogEntry
        if (last.before) {
          await writeDecision(db, last.rid, last.before)
          entry = { kind: 'set', decision: last.before }
        } else {
          await clearDecision(db, last.rid)
          entry = { kind: 'clear', txnId: last.txnId }
        }
        await appendLog(key, entry)
        forgetCases([last.rid])
        set((s) => {
          const resolved = new Set(s.resolved)
          resolved.delete(last.rid)
          const usedItems = new Map([...s.usedItems].filter(([, txnId]) => txnId !== last.txnId))
          last.before?.allocations.forEach((a) => usedItems.set(a.itemId, last.txnId))
          return {
            resolved,
            usedItems,
            benchRid: last.rid,
            benchPos: last.pos,
            cursorPos: last.pos ?? s.cursorPos,
            screen: 'review',
            revision: s.revision + 1,
            queueVersion: s.queueVersion + 1,
          }
        })
        get().notify('ok', `Deshecho: ${last.txnId} vuelve a estar como antes.`)
      })
      if (undoneBatches.length) {
        await get().refreshQueue()
        get().notify('ok', `Lote deshecho: ${formatCount(undoneBatches[0])} movimientos vuelven a estar pendientes.`)
      } else {
        void get().refreshCounts()
      }
    },

    bulkConfirm: async (minConf) => {
      const { db, key } = get()
      if (!db) return 0
      await writeChain
      const result = await confirmBulk(db, minConf)
      if (!result.count) return 0
      await appendLog(key, { kind: 'bulk', batch: result.batch, csv: result.csv, source: 'bulk', at: Date.now() })
      forgetCases(result.rids)
      set((s) => ({
        undo: [...s.undo.slice(-99), { kind: 'batch', batch: result.batch, count: result.count, rids: result.rids }],
        revision: s.revision + 1,
      }))
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
      forgetCases(result.rids)
      set((s) => ({ revision: s.revision + 1 }))
      await get().refreshQueue()
      return `Importados ${result.applied}. Ya decididos aquí, sin cambios: ${result.skipped}. Movimientos desconocidos: ${result.unknown}.`
    },

    notify: (tone, text, undoable = false) => {
      // An error stays until it is read: a fast reviewer's next confirmation must not wipe it.
      if (tone === 'ok' && get().notice?.tone === 'error') return
      noticeId += 1
      const id = noticeId
      set({ notice: { tone, text, id, undoable } })
      if (tone === 'ok') {
        setTimeout(() => {
          if (get().notice?.id === id) set({ notice: null })
        }, undoable ? NOTICE_UNDO_MS : NOTICE_MS)
      }
    },

    dismissNotice: () => set({ notice: null }),
    setHelp: (helpOpen) => set({ helpOpen }),
    setBulkOpen: (bulkOpen) => set({ bulkOpen }),
    toggleList: () => set({ listHidden: !get().listHidden }),
    draftFor: (rid) => drafts.get(rid) ?? null,
    rememberDraft: (rid, draft) => {
      drafts.set(rid, draft)
    },
    flushWrites: () => writeChain,
    setExportOptions: (options) => set({ exportOptions: { ...get().exportOptions, ...options } }),
  }
})
