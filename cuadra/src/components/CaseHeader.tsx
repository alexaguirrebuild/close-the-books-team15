import { formatCount, formatMoney } from '../domain/money'
import { highlightSegments } from '../domain/printed'
import { BalanceBar } from './BalanceBar'
import type { CaseModel } from './useCaseModel'

type CaseHeaderProps = { model: CaseModel; pos: number | null; total: number }

/** Who the movement seems to come from: what the decision says, then the bank, then the best guess. */
function whoIs({ draft, items, txn, groups }: CaseModel): string | null {
  const chosen = draft.rows.map((row) => items.get(row.itemId)?.partyName).find(Boolean)
  return chosen ?? (txn.counterparty || groups.best[0]?.item.partyName) ?? null
}

export function CaseHeader({ model, pos, total }: CaseHeaderProps) {
  const { txn, data, draft, balance } = model
  const { tokens, matchedFragments, decision } = data
  const ranges = tokens.filter((token) => matchedFragments.includes(token.norm))
  const segments = highlightSegments(txn.description, ranges)
  const who = whoIs(model)

  return (
    <header className="case-head">
      <div className="case-figure">
        <div className="case-amount">
          {formatMoney(Math.abs(txn.amount))}
          <span className="case-currency">{txn.currency}</span>
        </div>
        <div className="case-flags">
          <span className={txn.amount < 0 ? 'pill' : 'pill pill-select'}>{txn.amount < 0 ? 'Pago' : 'Cobro'}</span>
          {decision && <span className="pill pill-balanced">Ya decidido</span>}
          {draft.touched && <span className="pill pill-review">Borrador sin guardar</span>}
        </div>
      </div>
      <div className={who ? 'case-who' : 'case-who is-unknown'} title={who ?? undefined}>{who ?? 'Tercero sin identificar'}</div>
      <p className="case-concept">
        {txn.description
          ? segments.map((segment, index) =>
              segment.hit ? <mark key={index}>{segment.text}</mark> : <span key={index}>{segment.text}</span>,
            )
          : <span className="muted">El banco no da concepto.</span>}
      </p>
      <dl className="case-meta">
        <div><dt>Movimiento</dt><dd className="id txn-id">{txn.id}</dd></div>
        <div><dt>Fecha</dt><dd className="num">{txn.date}</dd></div>
        <div><dt>Cuenta</dt><dd className="id">{txn.account}</dd></div>
        {txn.counterpartyAccount && <div><dt>IBAN</dt><dd className="id">{txn.counterpartyAccount}</dd></div>}
        {txn.endToEnd && <div><dt>Ref. E2E</dt><dd className="id">{txn.endToEnd}</dd></div>}
        {txn.remittance && <div><dt>Remesa</dt><dd>{txn.remittance}</dd></div>}
        {txn.origAmount !== null && <div><dt>Original</dt><dd className="num">{formatMoney(txn.origAmount, txn.origCurrency)}</dd></div>}
        {pos !== null && <div><dt>Posición</dt><dd className="num">{formatCount(pos)} de {formatCount(total)}</dd></div>}
      </dl>
      <BalanceBar balance={balance} />
    </header>
  )
}
