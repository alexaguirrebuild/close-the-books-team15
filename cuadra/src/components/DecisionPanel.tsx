import { X } from 'lucide-react'
import { useState, type RefObject } from 'react'
import { buildEvidence, type EvidenceState } from '../domain/evidence'
import type { DiffState } from '../domain/goldenDiff'
import { formatMoney, formatPercent, parseAmount } from '../domain/money'
import { EXCEPTION_LABELS } from '../domain/types'
import { useStore } from '../store'
import type { CaseModel } from './useCaseModel'

const VISIBLE_ROWS = 20
const TRAIL_MARK: Record<EvidenceState, string> = { ok: '✓', warn: '!', bad: '×', none: '·' }

export function DecisionPanel({ model }: { model: CaseModel }) {
  const { draft, data, needsException, suggestions, fifoSwap, setOverlay, clearNoItem, save } = model
  const hasNoItem = !draft.rows.length && Boolean(draft.category || draft.exception)
  const confirming = draft.origin === 'proposal' && !draft.touched

  return (
    <aside className="decision" aria-label="Decisión">
      {data.decision && <p className="decided-note">Ya decidido. Guardar sustituye lo anterior.</p>}

      <div className="decision-mode" role="group" aria-label="Qué hace este movimiento">
        <button className={hasNoItem ? '' : 'is-active'} aria-pressed={!hasNoItem} onClick={() => hasNoItem && clearNoItem()}>
          Liquida partidas
        </button>
        <button className={hasNoItem ? 'is-active' : ''} aria-pressed={hasNoItem} onClick={() => setOverlay({ kind: 'category' })}>
          Sin partida
        </button>
      </div>

      <ProposalBlock model={model} />
      {hasNoItem ? <NoItemBlock model={model} /> : <RowsBlock model={model} />}

      {fifoSwap && (
        <p className="fifo-hint">Hay una partida igual más antigua, {fifoSwap.printed || fifoSwap.itemId}. Pulsa <kbd>F</kbd> para liquidar esa primero.</p>
      )}

      {needsException && (
        <div className="suggestion" role="status">
          <span>Falta la excepción. Probable: <strong>{EXCEPTION_LABELS[suggestions[0].code]}</strong></span>
          <span className="suggestion-why">{suggestions[0].why}</span>
        </div>
      )}

      <GoldenBlock model={model} />
      <DocsBlock model={model} />

      <div className="decision-cta">
        <button className="btn btn-primary btn-large case-save" onClick={() => save()}>
          {needsException ? 'Elegir excepción' : confirming ? 'Confirmar y seguir' : 'Guardar y seguir'}
          <kbd>Enter</kbd>
        </button>
        {needsException && (
          <button className="link footnote case-save-anyway" onClick={() => save(true)}>
            Guardar sin excepción (Shift+Enter)
          </button>
        )}
      </div>
    </aside>
  )
}

function ProposalBlock({ model }: { model: CaseModel }) {
  const { data, draft, items, balance, docs, txn, candidates, goldenDiff } = model
  const { proposal, tokens, matchedFragments } = data
  const confidence = proposal.find((row) => row.confidence !== null)?.confidence ?? null
  const partyName = draft.rows.map((row) => items.get(row.itemId)?.partyName).find(Boolean) ?? candidates[0]?.item.partyName ?? null
  const trail = buildEvidence({
    tokens,
    matchedFragments,
    partyName: partyName ?? null,
    counterparty: txn.counterparty,
    proposal,
    balanceTitle: balance.title,
    balanceStatus: balance.status,
    documents: docs.length,
    golden: goldenDiff,
  })

  return (
    <section className="decision-block">
      <div className="block-head">
        <h2 className="section-title">Por qué</h2>
        {confidence !== null && (
          <span className={confidence >= 0.95 ? 'pill pill-balanced proposal-conf' : 'pill pill-review proposal-conf'}>
            Confianza {formatPercent(confidence, 0)}
          </span>
        )}
      </div>
      <ol className="trail">
        {trail.map((step) => (
          <li key={step.id} className="trail-step" data-state={step.state}>
            <span className="trail-icon" aria-hidden="true">{TRAIL_MARK[step.state]}</span>
            <span className="trail-text"><span className="trail-label">{step.label}: </span>{step.text}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}

type AmountInputProps = {
  itemId: string
  amount: number
  amountRefs: RefObject<Map<string, HTMLInputElement>>
  onEdit: (itemId: string, amount: number) => void
}

/** Every keystroke reaches the draft, so Enter never saves a stale amount. */
function AmountInput({ itemId, amount, amountRefs, onEdit }: AmountInputProps) {
  const [text, setText] = useState(formatMoney(amount))
  const shown = parseAmount(text) === amount ? text : formatMoney(amount)
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
        const value = parseAmount(next)
        setText(next)
        if (value !== null) onEdit(itemId, Math.abs(value))
      }}
    />
  )
}

function RowsBlock({ model }: { model: CaseModel }) {
  const { draft, items, exception, setOverlay, editAmount, amountRefs, pickException } = model
  const [showAll, setShowAll] = useState(false)
  const rows = showAll ? draft.rows : draft.rows.slice(0, VISIBLE_ROWS)
  const several = draft.rows.length > 1

  return (
    <section className="decision-block">
      <div className="block-head">
        <h2 className="section-title">Reparto{several ? ` en ${draft.rows.length} partidas` : ''}</h2>
        {exception && !several ? (
          <span className="exception-chip line-exception">
            {EXCEPTION_LABELS[exception]}
            <button aria-label="Quitar excepción" onClick={() => pickException(exception, null)}><X size={12} /></button>
          </span>
        ) : (
          <button className="link footnote" onClick={() => setOverlay({ kind: 'exception', itemId: null })}>
            <kbd>E</kbd> Excepción{several ? ' a todas' : ''}
          </button>
        )}
      </div>
      {draft.rows.length === 0 && <p className="muted footnote">Marca una partida con su número para repartir el importe.</p>}
      <div className="alloc">
        {rows.map((row) => {
          const item = items.get(row.itemId)
          return (
            <div className="alloc-row" key={row.itemId}>
              <span className="alloc-who">
                <span className="alloc-printed" title={row.itemId}>{item?.printed || row.itemId}</span>
                {item
                  ? <span className="alloc-open">Saldo {formatMoney(item.openAmount, item.currency)}</span>
                  : <span className="alloc-open">De una fase anterior: no está cargada</span>}
              </span>
              <span className="amount-input">
                <AmountInput itemId={row.itemId} amount={row.amount} amountRefs={amountRefs} onEdit={editAmount} />
                <span className="amount-ccy">{item?.currency}</span>
              </span>
              {several && item?.kind !== 'CN' && (
                <span className="alloc-exception">
                  {row.exception ? (
                    <button className="exception-chip" onClick={() => setOverlay({ kind: 'exception', itemId: row.itemId })}>
                      {EXCEPTION_LABELS[row.exception]}
                    </button>
                  ) : (
                    <button className="exception-add" onClick={() => setOverlay({ kind: 'exception', itemId: row.itemId })}>
                      + Excepción solo en esta
                    </button>
                  )}
                </span>
              )}
            </div>
          )
        })}
      </div>
      {draft.rows.length > VISIBLE_ROWS && (
        <button className="link alloc-more" onClick={() => setShowAll(!showAll)}>
          {showAll ? 'Ver menos' : `Ver las ${draft.rows.length} partidas`}
        </button>
      )}
    </section>
  )
}

function NoItemBlock({ model }: { model: CaseModel }) {
  const meta = useStore((s) => s.meta)
  const { draft, setOverlay, clearNoItem } = model
  const label = draft.category ? (meta?.categoryLabels.get(draft.category) ?? draft.category) : null
  return (
    <section className="decision-block">
      <div className="assign">
        <div className="assign-row">
          <span className="section-title">Categoría</span>
          {label ? (
            <span className="exception-chip category-chip">
              {label}
              <button aria-label="Quitar categoría" onClick={clearNoItem}><X size={12} /></button>
            </span>
          ) : (
            <button className="link" onClick={() => setOverlay({ kind: 'category' })}><kbd>G</kbd> Asignar</button>
          )}
        </div>
        <p className="footnote">{draft.category ? `Código ${draft.category}.` : 'Elige qué es este movimiento.'}</p>
      </div>
    </section>
  )
}

const DIFF_TEXT: Record<DiffState, string> = {
  ok: 'Bien',
  missing: 'Falta',
  extra: 'Sobra',
  amount: 'Importe',
  exception: 'Excepción',
}

/** On TRAIN and the sample only: what the labels expected, row by row, as the reviewer edits. */
function GoldenBlock({ model }: { model: CaseModel }) {
  const { goldenDiff, items } = model
  if (!goldenDiff || goldenDiff.ok || !goldenDiff.rows.length) return null
  const wrong = goldenDiff.rows.filter((row) => row.state !== 'ok')
  return (
    <section className="decision-block golden-diff" aria-label="Comparación con el golden">
      <div className="block-head">
        <h2 className="section-title">Frente al golden</h2>
        {goldenDiff.segment && <span className="pill">{goldenDiff.segment}</span>}
      </div>
      <ul>
        {wrong.slice(0, VISIBLE_ROWS).map((row) => (
          <li key={row.item} className="golden-row">
            <span className="pill pill-error">{DIFF_TEXT[row.state]}</span>
            <span className="id">{items.get(row.item)?.printed || row.item}</span>
            <span className="golden-values num">
              {row.state === 'exception'
                ? `${row.goldException ?? 'sin excepción'}, no ${row.myException ?? 'sin excepción'}`
                : row.state === 'amount'
                  ? `${formatMoney(row.goldAmount ?? 0)}, no ${formatMoney(row.myAmount ?? 0)}`
                  : formatMoney((row.goldAmount ?? row.myAmount) ?? 0)}
            </span>
          </li>
        ))}
      </ul>
      {wrong.length > VISIBLE_ROWS && <p className="footnote">Y {wrong.length - VISIBLE_ROWS} más.</p>}
    </section>
  )
}

function DocsBlock({ model }: { model: CaseModel }) {
  const { docs, openDoc } = model
  if (!docs.length) return null
  return (
    <section className="decision-block docs" aria-label="Documentos relacionados">
      <h2 className="section-title">Documentos que lo mencionan</h2>
      {docs.map((doc, index) => (
        <button key={`${doc.file}-${doc.page}`} className="doc" onClick={() => openDoc(doc.file)}>
          <span className="id">{index === 0 && <kbd>O</kbd>} {doc.file.split('/').at(-1)}, pág. {doc.page}</span>
          <span className="doc-snippet">{doc.snippet}</span>
        </button>
      ))}
    </section>
  )
}
