import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { searchPdfs, type CaseData } from '../data/caseLoader'
import { computeResidual, reallocate } from '../domain/allocation'
import { computeBalance } from '../domain/balance'
import { groupCandidates, rankCandidates } from '../domain/candidates'
import { findCombos } from '../domain/combos'
import {
  draftToDecision,
  initialDraft,
  lineException,
  withCategory,
  withException,
  withRowException,
  type Draft,
} from '../domain/draft'
import { diffAgainstGolden } from '../domain/goldenDiff'
import { convert } from '../domain/money'
import { suggestExceptions } from '../domain/policy'
import type { ExceptionCode, FxRate, Item } from '../domain/types'
import { useStore } from '../store'
import { useCaseDocs } from './useCaseDocs'

export type Overlay =
  | { kind: 'none' }
  | { kind: 'exception'; itemId: string | null }
  | { kind: 'category' }
  | { kind: 'search' }

export type OpenPdf = { name: string; url: string }

const MAX_COMBOS = 3
const NO_RATES: FxRate[] = []
const NO_TERMS = new Map<string, { pct: number; days: number }>()

/** Everything one movement needs on screen, and every way to change its decision. */
export function useCaseModel(data: CaseData) {
  const meta = useStore((s) => s.meta)
  const decideAndNext = useStore((s) => s.decideAndNext)
  const step = useStore((s) => s.step)
  const usedItems = useStore((s) => s.usedItems)
  const pdfFiles = useStore((s) => s.pdfFiles)
  const notify = useStore((s) => s.notify)
  const db = useStore((s) => s.db)
  const rememberDraft = useStore((s) => s.rememberDraft)

  const { txn } = data
  const rates = meta?.rates ?? NO_RATES
  const terms = meta?.terms ?? NO_TERMS

  const [draft, setDraft] = useState<Draft>(() => useStore.getState().draftFor(txn.rid) ?? initialDraft(data))
  const [overlay, setOverlay] = useState<Overlay>({ kind: 'none' })
  const [cursor, setCursor] = useState(0)
  const [extra, setExtra] = useState<Item[]>([])
  const [expanded, setExpanded] = useState({ party: false, others: false })
  const [pdf, setPdf] = useState<OpenPdf | null>(null)
  const amountRefs = useRef(new Map<string, HTMLInputElement>())

  // Not while the next one loads: a saved movement must not get its draft back.
  useEffect(() => {
    if (draft.touched && useStore.getState().benchRid === txn.rid) rememberDraft(txn.rid, draft)
  }, [draft, rememberDraft, txn.rid])

  const used = useMemo(() => {
    const merged = new Map(data.usedElsewhere)
    usedItems.forEach((txnId, itemId) => {
      if (txnId !== txn.id) merged.set(itemId, txnId)
    })
    return merged
  }, [data.usedElsewhere, usedItems, txn.id])

  const candidates = useMemo(() => {
    const ranked = rankCandidates(txn, data.raw, used)
    const known = new Set(ranked.map((c) => c.item.itemId))
    const searched = extra
      .filter((item) => !known.has(item.itemId))
      .map((item) => ({ item, reasons: ['search' as const], fragment: null, fifo: false, usedBy: used.get(item.itemId) ?? null }))
    return [...searched, ...ranked]
  }, [txn, data.raw, used, extra])

  const groups = useMemo(() => groupCandidates(candidates), [candidates])
  const showParty = expanded.party || !groups.best.length
  const visible = useMemo(
    () => [...groups.best, ...(showParty ? groups.party : []), ...(expanded.others ? groups.others : [])],
    [groups, showParty, expanded.others],
  )

  const items = useMemo(() => {
    const map = new Map(data.items)
    extra.forEach((item) => map.set(item.itemId, item))
    return map
  }, [data.items, extra])

  const residual = useMemo(() => computeResidual(txn, draft.rows, items, rates), [txn, draft.rows, items, rates])
  const exception = lineException(draft)

  const balance = useMemo(
    () =>
      computeBalance({
        txn,
        rows: draft.rows,
        items,
        rates,
        exception: draft.exception,
        category: draft.category,
        categoryLabel: draft.category ? (meta?.categoryLabels.get(draft.category) ?? null) : null,
      }),
    [txn, draft, items, rates, meta],
  )

  const suggestions = useMemo(() => {
    const selected = draft.rows.flatMap((row) => items.get(row.itemId) ?? [])
    return suggestExceptions({
      txn,
      rows: selected,
      terms,
      rates,
      alreadySettled: draft.rows.some((row) => used.has(row.itemId)),
      unbalanced: draft.rows.length > 0 && !residual.balanced,
    })
  }, [draft.rows, items, txn, terms, rates, used, residual.balanced])

  const needsException = suggestions.length > 0 && !exception

  const goldenDiff = useMemo(() => (data.golden.length ? diffAgainstGolden(draft, data.golden) : null), [draft, data.golden])

  const combos = useMemo(() => {
    if (residual.balanced && draft.rows.length) return []
    const pool = data.raw.party
      .filter((item) => !used.has(item.itemId))
      .flatMap((item) => {
        const size = Math.abs(item.openAmount)
        const value = convert(item.kind === 'CN' ? -size : size, item.currency, txn.currency, txn.date, rates)
        return value === null ? [] : [{ id: item.itemId, value }]
      })
    return findCombos(pool, Math.abs(txn.amount), { limit: MAX_COMBOS })
  }, [data.raw.party, used, txn, rates, residual.balanced, draft.rows.length])

  const fifoSwap = useMemo(() => {
    if (draft.rows.length !== 1) return null
    const selected = candidates.find((c) => c.item.itemId === draft.rows[0].itemId)
    if (!selected || selected.fifo || selected.reasons.includes('printed')) return null
    return (
      candidates.find(
        (c) =>
          c.fifo &&
          c.item.partyId === selected.item.partyId &&
          c.item.currency === selected.item.currency &&
          c.item.openAmount === selected.item.openAmount,
      )?.item ?? null
    )
  }, [draft.rows, candidates])

  const docs = useCaseDocs(db, data, candidates, searchPdfs)

  /** Changing the selection keeps what the reviewer already did on the rows that stay. */
  const setRows = useCallback(
    (nextIds: string[], added: Item[] = []) => {
      const known = new Map([...items, ...added.map((item) => [item.itemId, item] as const)])
      setDraft((current) => ({
        ...current,
        rows: reallocate({ current: current.rows, nextIds, items: known, txn, rates }),
        exception: null,
        category: nextIds.length ? null : current.category,
        touched: true,
      }))
    },
    [txn, rates, items],
  )

  const toggle = useCallback(
    (item: Item) => {
      const selectedIds = draft.rows.map((row) => row.itemId)
      const nextIds = selectedIds.includes(item.itemId)
        ? selectedIds.filter((id) => id !== item.itemId)
        : [...selectedIds, item.itemId]
      setRows(nextIds, [item])
    },
    [draft.rows, setRows],
  )

  const applyCombo = useCallback(
    (index: number) => {
      const combo = combos[index]
      if (combo) setRows(combo.ids)
    },
    [combos, setRows],
  )

  /** What is on screen can lag behind the queue for a moment while the next movement loads. */
  const isCurrent = useCallback(() => useStore.getState().benchRid === txn.rid, [txn.rid])

  const save = useCallback(
    (force = false) => {
      if (!isCurrent()) return
      const decision = draftToDecision(txn.id, draft, Date.now())
      if (!decision) {
        notify('ok', 'Elige una partida o una categoría antes de confirmar.')
        return
      }
      if (needsException && !force) {
        setOverlay({ kind: 'exception', itemId: null })
        return
      }
      void decideAndNext(txn.rid, decision)
    },
    [txn, draft, needsException, decideAndNext, notify, isCurrent],
  )

  const markUnidentified = useCallback(() => {
    if (!isCurrent()) return
    void decideAndNext(txn.rid, { txnId: txn.id, allocations: [], category: 'UNIDENTIFIED', exception: 'UNIDENTIFIED', source: 'manual', at: Date.now() })
  }, [txn, decideAndNext, isCurrent])

  const pickException = useCallback(
    (code: ExceptionCode, itemId: string | null) => {
      const context = { items, txn, rates }
      setDraft((current) => (itemId ? withRowException(current, itemId, code, context) : withException(current, code, context)))
      setOverlay({ kind: 'none' })
    },
    [items, txn, rates],
  )

  const pickCategory = useCallback((category: string) => {
    setDraft((current) => withCategory(current, category))
    setOverlay({ kind: 'none' })
  }, [])

  const clearNoItem = useCallback(() => {
    setDraft((current) => ({ ...current, category: null, exception: null, touched: true }))
  }, [])

  const editAmount = useCallback((itemId: string, amount: number) => {
    setDraft((current) => ({
      ...current,
      touched: true,
      rows: current.rows.map((row) => (row.itemId === itemId ? { ...row, amount } : row)),
    }))
  }, [])

  const addSearched = useCallback(
    (item: Item) => {
      setExtra((current) => [item, ...current.filter((i) => i.itemId !== item.itemId)])
      toggle(item)
      setOverlay({ kind: 'none' })
    },
    [toggle],
  )

  const pdfUrl = useRef<string | null>(null)
  const pdfRun = useRef(0)

  const releasePdf = useCallback(() => {
    pdfRun.current += 1
    if (pdfUrl.current) URL.revokeObjectURL(pdfUrl.current)
    pdfUrl.current = null
  }, [])

  const closeDoc = useCallback(() => {
    releasePdf()
    setPdf(null)
  }, [releasePdf])

  const openDoc = useCallback(
    (path: string) => {
      const file = pdfFiles.find((f) => f.path === path || f.path.endsWith(path))
      if (!file) {
        notify('error', `${path} no está en la carpeta cargada.`)
        return
      }
      releasePdf()
      const run = pdfRun.current
      void file.open().then(
        (document) => {
          // Closed, replaced or moved on while the file was opening.
          if (run !== pdfRun.current) return
          pdfUrl.current = URL.createObjectURL(document)
          setPdf({ name: path.split('/').at(-1) ?? path, url: pdfUrl.current })
        },
        (error: unknown) => notify('error', `No se pudo abrir ${path}: ${String(error)}`),
      )
    },
    [pdfFiles, notify, releasePdf],
  )

  useEffect(() => releasePdf, [releasePdf])

  return {
    data,
    txn,
    rates,
    draft,
    exception,
    overlay,
    setOverlay,
    cursor,
    setCursor,
    expanded,
    setExpanded,
    showParty,
    pdf,
    used,
    candidates,
    groups,
    visible,
    items,
    residual,
    balance,
    suggestions,
    needsException,
    goldenDiff,
    combos,
    fifoSwap,
    docs,
    amountRefs,
    setRows,
    toggle,
    applyCombo,
    save,
    markUnidentified,
    pickException,
    pickCategory,
    clearNoItem,
    editAmount,
    addSearched,
    openDoc,
    closeDoc,
    step,
  }
}

export type CaseModel = ReturnType<typeof useCaseModel>
