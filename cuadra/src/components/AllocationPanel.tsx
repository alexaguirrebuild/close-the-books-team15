import { X } from 'lucide-react'
import { useState, type MutableRefObject } from 'react'
import type { CaseData } from '../data/caseLoader'
import type { ResidualResult } from '../domain/allocation'
import type { Draft } from '../domain/draft'
import { formatMoney, tolerance } from '../domain/money'
import { EXCEPTION_LABELS, type ExceptionCode, type Item } from '../domain/types'
import { Robot, type RobotMood } from './Robot'

type AllocationPanelProps = {
  draft: Draft
  data: CaseData
  items: Map<string, Item>
  residual: ResidualResult
  warned: boolean
  suggested: ExceptionCode[]
  fifoSwap: Item | null
  amountRefs: MutableRefObject<Map<string, HTMLInputElement>>
  onEditAmount: (itemId: string, amount: number) => void
  onOpenExceptions: () => void
  onOpenCategories: () => void
  onClearException: () => void
  onSave: () => void
}

function moodFor(draft: Draft, residual: ResidualResult, warned: boolean, pending: boolean): RobotMood {
  if (warned || residual.missingRate) return 'worried'
  if (!draft.rows.length) return draft.exception || draft.category ? 'happy' : 'neutral'
  if (!pending || draft.exception) return 'happy'
  return 'neutral'
}

function statusText(draft: Draft, residual: ResidualResult, currency: string): string {
  if (residual.missingRate) return 'Falta el tipo de cambio de una divisa.'
  if (!draft.rows.length) {
    if (draft.category) return `Sin partida, con category ${draft.category}.`
    if (draft.exception) return 'Sin partida, con excepción.'
    return 'Elige qué liquida este movimiento.'
  }
  if (residual.balanced && residual.openGap > tolerance(residual.applied)) {
    return `Cubre el movimiento, pero quedan ${formatMoney(residual.openGap, currency)} sin pagar en la partida.`
  }
  if (residual.balanced) return 'Cuadra.'
  const gap = formatMoney(Math.abs(residual.residual), currency)
  return residual.residual > 0 ? `Sobran ${gap} del movimiento.` : `Faltan ${gap} para cubrir las partidas.`
}

type AmountInputProps = {
  itemId: string
  amount: number
  amountRefs: MutableRefObject<Map<string, HTMLInputElement>>
  onEdit: (itemId: string, amount: number) => void
}

/** Every keystroke reaches the draft, so Enter never saves a stale amount. */
function AmountInput({ itemId, amount, amountRefs, onEdit }: AmountInputProps) {
  const [text, setText] = useState(amount.toFixed(2))
  const shown = Number(text.replace(',', '.')) === amount ? text : amount.toFixed(2)
  return (
    <input
      ref={(el) => {
        if (el) amountRefs.current.set(itemId, el)
        else amountRefs.current.delete(itemId)
      }}
      inputMode="decimal"
      value={shown}
      aria-label={`Importe aplicado a ${itemId}`}
      onFocus={(event) => event.target.select()}
      onChange={(event) => {
        const next = event.target.value
        const value = Number(next.replace(',', '.'))
        setText(next)
        if (next !== '' && next !== '-' && Number.isFinite(value)) onEdit(itemId, value)
      }}
    />
  )
}

export function AllocationPanel(props: AllocationPanelProps) {
  const { draft, data, items, residual, warned, suggested, fifoSwap, amountRefs } = props
  const { txn } = data
  const needsException = suggested.length > 0 && !draft.exception
  const mood = moodFor(draft, residual, warned, suggested.length > 0)

  return (
    <section className="allocation" aria-label="Reparto del movimiento">
      <div className="balance" data-mood={mood}>
        <Robot mood={mood} size={44} label={statusText(draft, residual, txn.currency)} />
        <div className="balance-text" role="status">
          <span className="balance-status">{statusText(draft, residual, txn.currency)}</span>
          {draft.rows.length > 0 && (
            <span className="balance-sum num">
              Aplicado {formatMoney(residual.applied, txn.currency)} de {formatMoney(Math.abs(txn.amount), txn.currency)}
            </span>
          )}
        </div>
      </div>

      {draft.rows.length > 0 && (
        <table className="alloc-table">
          <thead>
            <tr><th>Partida</th><th className="right">amount</th></tr>
          </thead>
          <tbody>
            {draft.rows.map((row) => {
              const item = items.get(row.itemId)
              return (
                <tr key={row.itemId}>
                  <td>
                    <span className="mono-small">{row.itemId}</span>
                    {item ? (
                      <span className="alloc-open num">Saldo {formatMoney(item.openAmount, item.currency)}</span>
                    ) : (
                      <span className="alloc-open problem">No existe en los ficheros de partidas</span>
                    )}
                  </td>
                  <td className="right">
                    <span className="amount-input">
                      <AmountInput
                        itemId={row.itemId}
                        amount={row.amount}
                        amountRefs={amountRefs}
                        onEdit={props.onEditAmount}
                      />
                      <span className="amount-ccy">{item?.currency}</span>
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {fifoSwap && (
        <p className="hint">
          Hay una partida igual más antigua, {fifoSwap.itemId}. Pulsa <kbd>F</kbd> para liquidar esa primero.
        </p>
      )}

      {needsException && (
        <p className={warned ? 'hint hint-strong' : 'hint'}>
          {warned ? 'Falta la excepción. Pulsa Enter otra vez para guardarlo así o elige una con E.' : `Lleva excepción. Probable: ${suggested.slice(0, 2).map((s) => EXCEPTION_LABELS[s]).join(' o ')}.`}
        </p>
      )}

      <dl className="assign">
        <div>
          <dt>Excepción</dt>
          <dd>
            {draft.exception ? (
              <span className="chip">
                {draft.exception}
                <button aria-label="Quitar excepción" onClick={props.onClearException}><X size={12} /></button>
              </span>
            ) : (
              <button className="link" onClick={props.onOpenExceptions}><kbd>E</kbd> Elegir</button>
            )}
          </dd>
        </div>
        <div>
          <dt>Category</dt>
          <dd>
            {draft.category ? (
              <span className="chip">{draft.category}</span>
            ) : draft.rows.length ? (
              <span className="muted">Solo sin partida</span>
            ) : (
              <button className="link" onClick={props.onOpenCategories}><kbd>G</kbd> Asignar</button>
            )}
          </dd>
        </div>
      </dl>

      <button className="cta cta-large cta-wide" onClick={props.onSave}>
        {draft.origin === 'proposal' && !draft.touched ? 'Confirmar propuesta y seguir' : 'Guardar y seguir'}
        <span className="cta-box"><kbd className="kbd-invert">Enter</kbd></span>
      </button>
    </section>
  )
}
