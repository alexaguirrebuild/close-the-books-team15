import { useCallback, useEffect, useRef, useState } from 'react'
import { proposalDecision } from '../domain/draft'
import { formatCount, formatMoney, formatPercent } from '../domain/money'
import {
  bulkEligibleCount,
  CASE_TYPE_LABEL,
  queuePage,
  STATUS_LABEL,
  type CaseType,
  type QueueRow,
  type QueueSort,
  type QueueStatus,
} from '../data/queue'
import { useStore } from '../store'

const PAGE_SIZE = 100

const SORTS: { value: QueueSort; label: string }[] = [
  { value: 'amount', label: 'Mayor importe' },
  { value: 'confidence_low', label: 'Menor confianza' },
  { value: 'confidence_high', label: 'Mayor confianza' },
]

export function QueueScreen() {
  const db = useStore((s) => s.db)
  const meta = useStore((s) => s.meta)
  const filters = useStore((s) => s.filters)
  const setFilters = useStore((s) => s.setFilters)
  const queueTotal = useStore((s) => s.queueTotal)
  const queueVersion = useStore((s) => s.queueVersion)
  const cursorPos = useStore((s) => s.cursorPos)
  const setCursor = useStore((s) => s.setCursor)
  const resolved = useStore((s) => s.resolved)
  const openCase = useStore((s) => s.openCase)
  const [rows, setRows] = useState<QueueRow[]>([])
  const [partyDraft, setPartyDraft] = useState(filters.party)
  const [failure, setFailure] = useState<string | null>(null)
  const partyRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLTableSectionElement>(null)

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
    const handle = setTimeout(() => {
      if (partyDraft !== useStore.getState().filters.party) void setFilters({ party: partyDraft })
    }, 250)
    return () => clearTimeout(handle)
  }, [partyDraft, setFilters])

  useEffect(() => {
    listRef.current?.querySelector('.is-cursor')?.scrollIntoView({ block: 'nearest' })
  }, [cursorPos, rows])

  const currentRow = rows.find((r) => r.pos === cursorPos)

  const confirmCurrent = useCallback(async () => {
    if (!currentRow) return
    const state = useStore.getState()
    const data = await state.caseFor(currentRow.rid)
    const decision = proposalDecision(data, Date.now())
    if (!decision) {
      state.notify('ok', `${currentRow.id} no tiene propuesta que confirmar. Ábrelo con Enter.`)
      return
    }
    state.decide(currentRow.rid, decision)
    setCursor(Math.min(cursorPos + 1, queueTotal))
  }, [currentRow, cursorPos, queueTotal, setCursor])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey || event.metaKey || event.ctrlKey || useStore.getState().helpOpen) return
      if (event.target instanceof HTMLElement && ['INPUT', 'SELECT'].includes(event.target.tagName)) {
        if (event.key === 'Escape' || event.key === 'Enter') event.target.blur()
        return
      }
      const move = (delta: number) => {
        event.preventDefault()
        setCursor(Math.min(Math.max(cursorPos + delta, 1), Math.max(queueTotal, 1)))
      }
      if (event.key === 'j' || event.key === 'ArrowDown') move(1)
      else if (event.key === 'k' || event.key === 'ArrowUp') move(-1)
      else if (event.key === 'PageDown') move(PAGE_SIZE)
      else if (event.key === 'PageUp') move(-PAGE_SIZE)
      else if (event.key === 'Home') move(-queueTotal)
      else if (event.key === '/') {
        event.preventDefault()
        partyRef.current?.focus()
      } else if (event.key === 'Enter' && currentRow) {
        event.preventDefault()
        openCase(currentRow.rid, currentRow.pos)
      } else if (event.key === 'c' || event.key === 'C') {
        event.preventDefault()
        void confirmCurrent()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [cursorPos, queueTotal, currentRow, openCase, confirmCurrent, setCursor])

  return (
    <div className="queue">
      <div className="filters" role="group" aria-label="Filtros">
        <label>
          <span className="label">Estado</span>
          <select value={filters.status} onChange={(e) => void setFilters({ status: e.target.value as QueueStatus })}>
            {Object.entries(STATUS_LABEL).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          <span className="label">Divisa</span>
          <select value={filters.currency} onChange={(e) => void setFilters({ currency: e.target.value })}>
            <option value="">Todas</option>
            {meta?.currencies.map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>
        <label>
          <span className="label">Cuenta</span>
          <select value={filters.account} onChange={(e) => void setFilters({ account: e.target.value })}>
            <option value="">Todas</option>
            {meta?.accounts.map((a) => <option key={a}>{a}</option>)}
          </select>
        </label>
        <label className="filter-grow">
          <span className="label">Tercero, concepto o id</span>
          <input ref={partyRef} value={partyDraft} placeholder="Pulsa / para buscar" onChange={(e) => setPartyDraft(e.target.value)} />
        </label>
        <label>
          <span className="label">Tipo de caso</span>
          <select value={filters.caseType} onChange={(e) => void setFilters({ caseType: e.target.value as CaseType | '' })}>
            <option value="">Todos</option>
            {Object.entries(CASE_TYPE_LABEL).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          <span className="label">Ordenar por</span>
          <select value={filters.sort} onChange={(e) => void setFilters({ sort: e.target.value as QueueSort })}>
            {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </label>
      </div>

      <BulkBar disabled={!meta?.hasProposals} />

      {failure && <p className="problem page">No se pudo leer la cola: {failure}</p>}

      <div className="table-scroll">
        <table className="table table-queue">
          <thead>
            <tr>
              <th className="right">Importe</th>
              <th>Fecha</th>
              <th>Cuenta</th>
              <th>Concepto</th>
              <th>Tercero propuesto</th>
              <th>Caso</th>
              <th className="right">Confianza</th>
            </tr>
          </thead>
          <tbody ref={listRef}>
            {rows.map((row) => {
              const done = row.resolved || resolved.has(row.rid)
              return (
                <tr
                  key={row.pos}
                  className={`${row.pos === cursorPos ? 'is-cursor ' : ''}${done ? 'is-done' : ''}`}
                  aria-selected={row.pos === cursorPos}
                  onClick={() => setCursor(row.pos)}
                  onDoubleClick={() => openCase(row.rid, row.pos)}
                >
                  <td className={`right num ${row.amount < 0 ? 'neg' : ''}`}>{formatMoney(row.amount, row.currency)}</td>
                  <td className="num nowrap">{row.date}</td>
                  <td className="mono-small nowrap">{row.account}</td>
                  <td className="cell-concept" title={row.description}>{row.description}</td>
                  <td className="cell-party" title={row.partyName ?? ''}>{row.partyName ?? <span className="muted">Sin identificar</span>}</td>
                  <td className="nowrap">{done ? 'Resuelto' : CASE_TYPE_LABEL[row.caseType]}</td>
                  <td className="right num">{row.conf === null ? <span className="muted">Ninguna</span> : formatPercent(row.conf, 0)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!rows.length && !failure && <QueueEmpty hasFilters={Boolean(filters.currency || filters.account || filters.party || filters.caseType || filters.status !== 'pending')} />}
      </div>

      <footer className="queue-foot">
        <span className="num">
          {queueTotal ? `${formatCount(offset + 1)}–${formatCount(Math.min(offset + PAGE_SIZE, queueTotal))} de ${formatCount(queueTotal)} ${STATUS_LABEL[filters.status].toLowerCase()}` : `0 ${STATUS_LABEL[filters.status].toLowerCase()}`}
        </span>
        <span className="key-legend">
          <kbd>J</kbd><kbd>K</kbd> mover <kbd>Enter</kbd> abrir <kbd>C</kbd> confirmar <kbd>Av Pág</kbd> página <kbd>?</kbd> ayuda
        </span>
      </footer>
    </div>
  )
}

function QueueEmpty({ hasFilters }: { hasFilters: boolean }) {
  return (
    <div className="empty">
      {hasFilters ? 'Ningún movimiento con estos filtros. Quítalos para ver el resto.' : 'No queda nada pendiente. Valida y descarga el CSV en Exportar.'}
    </div>
  )
}

function BulkBar({ disabled }: { disabled: boolean }) {
  const db = useStore((s) => s.db)
  const queueVersion = useStore((s) => s.queueVersion)
  const bulkConfirm = useStore((s) => s.bulkConfirm)
  const notify = useStore((s) => s.notify)
  const [threshold, setThreshold] = useState(95)
  const [eligible, setEligible] = useState(0)
  const [armed, setArmed] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!db || disabled) return
    let current = true
    void bulkEligibleCount(db, threshold / 100).then((n) => {
      if (current) {
        setEligible(n)
        setArmed(false)
      }
    })
    return () => {
      current = false
    }
  }, [db, threshold, queueVersion, disabled])

  if (disabled) return null

  const run = async () => {
    if (!armed) {
      setArmed(true)
      return
    }
    setBusy(true)
    try {
      const count = await bulkConfirm(threshold / 100)
      notify('ok', `Confirmados ${formatCount(count)} movimientos. Pulsa Z para deshacer el lote.`)
    } catch (error) {
      notify('error', `No se pudo confirmar el lote: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setBusy(false)
      setArmed(false)
    }
  }

  return (
    <div className="bulk">
      <label>
        Propuestas con confianza desde
        <input type="number" min={0} max={100} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} />%
      </label>
      <span className="num">{formatCount(eligible)} en esta vista</span>
      <button className={armed ? 'btn btn-armed' : 'btn'} disabled={!eligible || busy} onClick={() => void run()}>
        {busy ? 'Confirmando…' : armed ? `Confirmar ${formatCount(eligible)} de una vez` : 'Confirmar en lote'}
      </button>
    </div>
  )
}
