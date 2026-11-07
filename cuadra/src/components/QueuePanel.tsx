import { ChevronLeft, ChevronRight, SlidersHorizontal, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { availableViews, CASE_TYPE_LABEL, queuePage, VIEW_LABEL, viewHint, type QueueFilters, type QueueRow, type QueueView } from '../data/queue'
import { formatCount, formatMoney, formatPercent } from '../domain/money'
import { EXCEPTION_LABELS, isException } from '../domain/types'
import { useStore } from '../store'
import { FilterPanel } from './FilterPanel'

export const SEARCH_INPUT_ID = 'queue-search'
const PAGE_SIZE = 100
const SEARCH_DEBOUNCE_MS = 250

export function QueuePanel() {
  const db = useStore((s) => s.db)
  const filters = useStore((s) => s.filters)
  const queueTotal = useStore((s) => s.queueTotal)
  const queueVersion = useStore((s) => s.queueVersion)
  const cursorPos = useStore((s) => s.cursorPos)
  const jumpTo = useStore((s) => s.jumpTo)
  const [rows, setRows] = useState<QueueRow[]>([])
  const [failure, setFailure] = useState<string | null>(null)
  const listRef = useRef<HTMLUListElement>(null)

  const page = Math.floor((Math.max(cursorPos, 1) - 1) / PAGE_SIZE)
  const offset = page * PAGE_SIZE

  useEffect(() => {
    if (!db) return
    let current = true
    queuePage(db, offset, PAGE_SIZE).then(
      (next) => {
        if (current) {
          setRows(next)
          setFailure(null)
        }
      },
      (error: unknown) => setFailure(error instanceof Error ? error.message : String(error)),
    )
    return () => {
      current = false
    }
  }, [db, offset, queueVersion])

  useEffect(() => {
    listRef.current?.querySelector('.is-cursor')?.scrollIntoView({ block: 'nearest' })
  }, [cursorPos, rows])

  return (
    <section className="queue" aria-label="Movimientos">
      <div className="queue-top">
        <QueueViews />
        <ViewHint view={filters.view} />
        <QueueSearch />
        <FilterChips />
        {filters.view === 'ready' && <BulkRow />}
      </div>
      {failure && <p className="problem page-message">No se pudo leer la lista: {failure}</p>}
      <ul className="queue-list" ref={listRef}>
        {rows.map((row) => <QueueItem key={row.pos} row={row} />)}
        {!rows.length && !failure && <QueueEmpty filters={filters} />}
      </ul>
      <footer className="queue-foot">
        <span className="num queue-range">
          {queueTotal ? `${formatCount(offset + 1)}–${formatCount(Math.min(offset + PAGE_SIZE, queueTotal))} de ${formatCount(queueTotal)}` : 'Ninguno'}
        </span>
        <span className="pager">
          <button aria-label="Página anterior" disabled={offset === 0} onClick={() => void jumpTo(offset - PAGE_SIZE + 1)}>
            <ChevronLeft size={15} />
          </button>
          <button aria-label="Página siguiente" disabled={offset + PAGE_SIZE >= queueTotal} onClick={() => void jumpTo(offset + PAGE_SIZE + 1)}>
            <ChevronRight size={15} />
          </button>
        </span>
      </footer>
    </section>
  )
}

function QueueViews() {
  const filters = useStore((s) => s.filters)
  const counts = useStore((s) => s.counts)
  const meta = useStore((s) => s.meta)
  const setFilters = useStore((s) => s.setFilters)
  const views = availableViews(meta)

  return (
    <div className="views" role="tablist" aria-label="Vistas">
      {views.map((view) => (
        <button
          key={view}
          role="tab"
          aria-selected={filters.view === view}
          className={filters.view === view ? 'view-tab is-active' : 'view-tab'}
          onClick={() => void setFilters({ view })}
        >
          {VIEW_LABEL[view]}
          {counts && <span className="view-count">{formatCount(counts[view])}</span>}
        </button>
      ))}
    </div>
  )
}

function ViewHint({ view }: { view: QueueView }) {
  const hasConfidence = useStore((s) => Boolean(s.meta?.hasConfidence))
  return <p className="view-hint">{viewHint(view, hasConfidence)}</p>
}

function QueueSearch() {
  const filters = useStore((s) => s.filters)
  const setFilters = useStore((s) => s.setFilters)
  const [term, setTerm] = useState(filters.party)
  const [open, setOpen] = useState(false)
  const active = [filters.currency, filters.account, filters.entity, filters.caseType, filters.segment].filter(Boolean).length

  useEffect(() => {
    const handle = setTimeout(() => {
      if (term !== useStore.getState().filters.party) void setFilters({ party: term })
    }, SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(handle)
  }, [term, setFilters])

  return (
    <div className="queue-search filter-popover">
      <input
        id={SEARCH_INPUT_ID}
        value={term}
        placeholder="Buscar (B): tercero, concepto, id"
        aria-label="Buscar movimiento"
        onChange={(event) => setTerm(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape' || event.key === 'Enter') event.currentTarget.blur()
        }}
      />
      <button className={open ? 'btn is-active' : 'btn'} aria-expanded={open} onClick={() => setOpen(!open)}>
        <SlidersHorizontal size={14} aria-hidden="true" />
        Filtros
        {active > 0 && <span className="filter-count">{active}</span>}
      </button>
      {open && <FilterPanel onClose={() => setOpen(false)} />}
    </div>
  )
}

function FilterChips() {
  const filters = useStore((s) => s.filters)
  const setFilters = useStore((s) => s.setFilters)
  const chips: { key: keyof QueueFilters; label: string }[] = [
    ...(filters.currency ? [{ key: 'currency' as const, label: filters.currency }] : []),
    ...(filters.entity ? [{ key: 'entity' as const, label: filters.entity }] : []),
    ...(filters.account ? [{ key: 'account' as const, label: filters.account }] : []),
    ...(filters.caseType ? [{ key: 'caseType' as const, label: CASE_TYPE_LABEL[filters.caseType] }] : []),
    ...(filters.segment ? [{ key: 'segment' as const, label: filters.segment }] : []),
  ]
  if (!chips.length) return null
  return (
    <div className="filter-chips">
      {chips.map((chip) => (
        <span key={chip.key} className="filter-chip">
          {chip.label}
          <button aria-label={`Quitar filtro ${chip.label}`} onClick={() => void setFilters({ [chip.key]: '' })}>
            <X size={12} />
          </button>
        </span>
      ))}
    </div>
  )
}

function BulkRow() {
  const filters = useStore((s) => s.filters)
  const setFilters = useStore((s) => s.setFilters)
  const setBulkOpen = useStore((s) => s.setBulkOpen)
  const counts = useStore((s) => s.counts)
  const [percent, setPercent] = useState(Math.round(filters.readyThreshold * 100))
  const eligible = counts?.ready ?? 0

  useEffect(() => {
    const handle = setTimeout(() => {
      const threshold = percent / 100
      if (threshold !== useStore.getState().filters.readyThreshold) void setFilters({ readyThreshold: threshold })
    }, SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(handle)
  }, [percent, setFilters])

  return (
    <div className="bulk-row">
      <label>
        Desde
        <input type="number" min={0} max={100} value={percent} aria-label="Confianza mínima" onChange={(e) => setPercent(Number(e.target.value))} />%
      </label>
      <button className="btn btn-primary" disabled={!eligible} onClick={() => setBulkOpen(true)}>
        Confirmar {formatCount(eligible)}
      </button>
    </div>
  )
}

function rowState(row: QueueRow, done: boolean, threshold: number): { label: string; tone: string } {
  if (done) return { label: 'Hecho', tone: 'pill-balanced' }
  if (row.caseType === 'sin_propuesta') return { label: 'Sin propuesta', tone: '' }
  const confidence = row.conf === null ? '' : ` ${formatPercent(row.conf, 0)}`
  if (row.exception) {
    const name = isException(row.exception) ? EXCEPTION_LABELS[row.exception] : row.exception
    return { label: `${name}${confidence}`, tone: 'pill-review' }
  }
  if (row.conf === null) return { label: 'Propuesta', tone: '' }
  return row.conf >= threshold ? { label: `Lista${confidence}`, tone: 'pill-balanced' } : { label: `Revisar${confidence}`, tone: 'pill-review' }
}

function QueueItem({ row }: { row: QueueRow }) {
  const cursorPos = useStore((s) => s.cursorPos)
  const resolved = useStore((s) => s.resolved)
  const openCase = useStore((s) => s.openCase)
  const threshold = useStore((s) => s.filters.readyThreshold)
  const done = row.resolved || resolved.has(row.rid)
  const state = rowState(row, done, threshold)
  const who = row.partyName ?? row.counterparty

  return (
    <li>
      <button
        className={`queue-row${row.pos === cursorPos ? ' is-cursor' : ''}${done ? ' is-done' : ''}`}
        aria-current={row.pos === cursorPos ? 'true' : undefined}
        onClick={() => openCase(row.rid, row.pos)}
      >
        <span className="queue-line">
          <span className={who ? 'queue-who' : 'queue-who queue-who-text'} title={row.description}>{who ?? row.description}</span>
          <span className="queue-amount">{formatMoney(row.amount, row.currency)}</span>
        </span>
        <span className="queue-line">
          <span className="queue-sub num">{row.date}</span>
          <span className={`pill ${state.tone}`}>{state.label}</span>
        </span>
      </button>
    </li>
  )
}

function QueueEmpty({ filters }: { filters: QueueFilters }) {
  const narrowed = filters.currency || filters.account || filters.entity || filters.party || filters.caseType || filters.segment
  return (
    <li className="queue-empty">
      {narrowed ? 'Ningún movimiento con estos filtros. Quítalos para ver el resto.' : 'Nada en esta vista. Prueba con otra o valida el envío.'}
    </li>
  )
}
