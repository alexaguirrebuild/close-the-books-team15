import { ChevronDown, ChevronRight } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { describeFit, type Candidate, type CandidateReason, type Fit } from '../domain/candidates'
import { formatMoney } from '../domain/money'
import { COMBO_KEYS } from './useCaseKeys'
import type { CaseModel } from './useCaseModel'

const REASON: Partial<Record<CandidateReason, { label: string; tone: string }>> = {
  proposal: { label: 'Propuesta', tone: 'pill-select' },
  printed: { label: 'Número en el concepto', tone: 'pill-balanced' },
  exact: { label: 'Importe exacto', tone: 'pill-balanced' },
  converted: { label: 'Importe convertido', tone: '' },
  party: { label: 'Mismo tercero', tone: '' },
  search: { label: 'Buscada', tone: '' },
}

const CENT = 0.005

function FitLabel({ fit, selected, currency }: { fit: Fit; selected: boolean; currency: string }) {
  if (selected) return <span className="cand-fit">Aplicada</span>
  if (fit.fit === 'exact') return <span className="cand-fit is-exact">Cuadra</span>
  if (fit.fit === 'leaves') return <span className="cand-fit">Deja {formatMoney(fit.gap, currency)}</span>
  if (fit.fit === 'over') return <span className="cand-fit is-over">Se pasa {formatMoney(fit.gap, currency)}</span>
  return <span className="cand-fit">Sin tipo de cambio</span>
}

type RowProps = { candidate: Candidate; index: number; model: CaseModel; remaining: number }

function CandidateRow({ candidate, index, model, remaining }: RowProps) {
  const { draft, cursor, toggle, txn, rates, groups } = model
  const { item, reasons, fifo, usedBy } = candidate
  const selected = draft.rows.some((row) => row.itemId === item.itemId)
  const fit = describeFit(item, remaining, txn, rates)
  const isOtherParty = groups.others.includes(candidate)
  const className = `candidate${selected ? ' is-selected' : ''}${index === cursor ? ' is-cursor' : ''}${usedBy ? ' is-used' : ''}${item.isOpen ? '' : ' is-closed'}`

  return (
    <li className={className}>
      <button onClick={() => toggle(item)} aria-pressed={selected}>
        {index < 9 ? <kbd className="cand-key">{index + 1}</kbd> : <span className="cand-key" />}
        <span className="cand-main">
          <span className="cand-line">
            <span className="cand-printed">{item.printed || item.itemId}</span>
            <span className="cand-party" title={item.partyName}>{item.partyName}</span>
          </span>
          <span className="cand-line cand-sub">
            <span className="id item-id">{item.itemId}</span>
            <span className="num">{item.issueDate ? `Emitida ${item.issueDate}` : `Vence ${item.dueDate || 'sin fecha'}`}</span>
            {reasons.map((reason) => {
              const info = REASON[reason]
              return info ? <span key={reason} className={`pill ${info.tone}`}>{info.label}</span> : null
            })}
            {fifo && <span className="pill pill-select">La más antigua</span>}
            {isOtherParty && <span className="pill pill-review">Otro tercero</span>}
            {usedBy && <span className="pill pill-review">Ya en {usedBy}</span>}
            {!item.isOpen && <span className="pill pill-review">Emitida después</span>}
          </span>
        </span>
        <span className="cand-right">
          <span className="cand-amount">{formatMoney(item.openAmount, item.currency)}</span>
          {fit.equivalent !== null && <span className="cand-fit num">≈ {formatMoney(fit.equivalent, txn.currency)}</span>}
          <FitLabel fit={fit} selected={selected} currency={txn.currency} />
        </span>
      </button>
    </li>
  )
}

type ToggleProps = { open: boolean; count: number; label: string; note?: string; onClick: () => void; keyHint?: string }

function GroupToggle({ open, count, label, note, onClick, keyHint }: ToggleProps) {
  if (!count) return null
  return (
    <button className="group-toggle" aria-expanded={open} onClick={onClick}>
      {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
      {label} ({count})
      {note && <span className="group-note">{note}</span>}
      {keyHint && <kbd>{keyHint}</kbd>}
    </button>
  )
}

export function CandidateList({ model }: { model: CaseModel }) {
  const { groups, visible, combos, txn, cursor, residual, applyCombo, setOverlay, expanded, setExpanded, showParty, items } = model
  const listRef = useRef<HTMLDivElement>(null)
  const remaining = Math.abs(txn.amount) - residual.applied

  useEffect(() => {
    listRef.current?.querySelector('.candidate.is-cursor')?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  const rowsOf = (list: Candidate[]) => (
    <ol className="candidate-list">
      {list.map((candidate) => (
        <CandidateRow key={candidate.item.itemId} candidate={candidate} index={visible.indexOf(candidate)} model={model} remaining={remaining} />
      ))}
    </ol>
  )

  return (
    <section className="candidates" aria-label="Partidas candidatas" ref={listRef}>
      <div className="candidates-head">
        <h2 className="section-title">Partidas que podría liquidar</h2>
        <button className="link" onClick={() => setOverlay({ kind: 'search' })}><kbd>/</kbd> Buscar otra</button>
      </div>

      {visible.length === 0 && !groups.party.length && !groups.others.length && (
        <p className="candidates-empty">
          Nada coincide por número, importe ni tercero. Busca una partida con <kbd>/</kbd>, asigna una categoría con <kbd>G</kbd> o márcalo sin identificar con <kbd>U</kbd>.
        </p>
      )}

      {rowsOf(groups.best)}
      {combos.length > 0 && (
        <ul className="combos" aria-label="Combinaciones que cuadran">
          {combos.map((combo, index) => {
            const gap = Math.abs(combo.total - Math.abs(txn.amount))
            const printed = combo.ids.map((id) => items.get(id)?.printed || id)
            return (
              <li key={combo.ids.join('+')}>
                <button className="combo" onClick={() => applyCombo(index)}>
                  <kbd>{COMBO_KEYS[index].toUpperCase()}</kbd>
                  <span>{combo.ids.length} partidas</span>
                  <span className={gap < CENT ? 'pill pill-balanced' : 'pill'}>{gap < CENT ? 'Cuadra exacto' : `Diferencia ${formatMoney(gap, txn.currency)}`}</span>
                  <span className="combo-list id">{printed.join(' + ')}</span>
                </button>
              </li>
            )
          })}
        </ul>
      )}

      {groups.best.length > 0 && (
        <GroupToggle
          open={showParty}
          count={groups.party.length}
          label="Más partidas del tercero"
          keyHint="M"
          onClick={() => setExpanded({ ...expanded, party: !expanded.party })}
        />
      )}
      {showParty && rowsOf(groups.party)}
      <GroupToggle
        open={expanded.others}
        count={groups.others.length}
        label="Otros terceros con importe parecido"
        note="rara vez son la buena"
        onClick={() => setExpanded({ ...expanded, others: !expanded.others })}
      />
      {expanded.others && rowsOf(groups.others)}
    </section>
  )
}
