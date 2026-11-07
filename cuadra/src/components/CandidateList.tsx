import { useEffect, useRef } from 'react'
import type { Candidate, CandidateReason } from '../domain/candidates'
import type { Combo } from '../domain/combos'
import { formatMoney } from '../domain/money'
import type { Item, Txn } from '../domain/types'

const REASON_LABEL: Record<CandidateReason, string> = {
  proposal: 'Propuesta',
  printed: 'N.º en concepto',
  exact: 'Importe exacto',
  converted: 'Importe convertido',
  party: 'Mismo tercero',
  search: 'Buscada',
}

type CandidateListProps = {
  candidates: Candidate[]
  combos: Combo[]
  comboKeys: string[]
  selectedIds: string[]
  cursor: number
  txn: Txn
  onToggle: (item: Item) => void
  onApplyCombo: (index: number) => void
  onSearch: () => void
}

export function CandidateList(props: CandidateListProps) {
  const { candidates, combos, comboKeys, selectedIds, cursor, txn, onToggle, onApplyCombo, onSearch } = props
  const listRef = useRef<HTMLOListElement>(null)

  useEffect(() => {
    listRef.current?.querySelector('.is-cursor')?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  return (
    <section className="candidates" aria-label="Partidas candidatas">
      <div className="section-head">
        <h2 className="label">Partidas candidatas</h2>
        <button className="link" onClick={onSearch}><kbd>/</kbd> Buscar otra partida</button>
      </div>

      {combos.length > 0 && (
        <ul className="combos" aria-label="Combinaciones que cuadran">
          {combos.map((combo, index) => (
            <li key={combo.ids.join('+')}>
              <button className="combo" onClick={() => onApplyCombo(index)}>
                <kbd>{comboKeys[index].toUpperCase()}</kbd>
                <span className="combo-ids">{combo.ids.length} partidas suman {formatMoney(combo.total, txn.currency)}</span>
                <span className="combo-list mono-small">{combo.ids.join(' + ')}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {candidates.length === 0 ? (
        <div className="empty">
          Nada coincide por número, importe ni tercero. Busca con <kbd>/</kbd>, marca una excepción con <kbd>E</kbd> o asigna
          una category con <kbd>G</kbd>.
        </div>
      ) : (
        <ol className="candidate-list" ref={listRef}>
          {candidates.map((candidate, index) => {
            const { item } = candidate
            const selected = selectedIds.includes(item.itemId)
            return (
              <li
                key={item.itemId}
                className={`candidate${selected ? ' is-selected' : ''}${index === cursor ? ' is-cursor' : ''}${candidate.usedBy ? ' is-used' : ''}`}
              >
                <button onClick={() => onToggle(item)} aria-pressed={selected}>
                  {index < 9 ? <kbd className="cand-key">{index + 1}</kbd> : <span className="cand-key" />}
                  <span className="cand-main">
                    <span className="cand-line">
                      <span className="cand-printed">{item.printed || item.itemId}</span>
                      <span className="cand-party" title={item.partyName}>{item.partyName}</span>
                    </span>
                    <span className="cand-line cand-sub">
                      <span className="mono-small">{item.itemId}</span>
                      <span className="num">Vence {item.dueDate || 'sin fecha'}</span>
                      {candidate.reasons.map((reason) => (
                        <span key={reason} className={`tag tag-${reason}`}>{REASON_LABEL[reason]}</span>
                      ))}
                      {candidate.fifo && <span className="tag tag-fifo">FIFO: la más antigua</span>}
                      {candidate.usedBy && <span className="tag tag-used">Ya en {candidate.usedBy}</span>}
                      {!item.isOpen && <span className="tag tag-used">No abierta</span>}
                    </span>
                  </span>
                  <span className="cand-amount num">{formatMoney(item.openAmount, item.currency)}</span>
                </button>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
