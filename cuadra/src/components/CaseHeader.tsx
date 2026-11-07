import type { CaseData } from '../data/caseLoader'
import { formatCount, formatMoney, formatPercent } from '../domain/money'
import { highlightSegments } from '../domain/printed'

type CaseHeaderProps = { data: CaseData; pos: number | null; total: number }

export function CaseHeader({ data, pos, total }: CaseHeaderProps) {
  const { txn, tokens, matchedFragments, proposal, decision } = data
  const ranges = tokens.filter((t) => matchedFragments.includes(t.norm))
  const segments = highlightSegments(txn.description, ranges)
  const unmatched = tokens.filter((t) => !matchedFragments.includes(t.norm))
  const confidence = proposal.find((p) => p.confidence !== null)?.confidence ?? null
  const reason = proposal.find((p) => p.reason)?.reason

  return (
    <header className="case-header">
      <div className="case-amount">
        <div className={`amount-figure${txn.amount < 0 ? ' neg' : ''}`}>{formatMoney(txn.amount)}</div>
        <div className="amount-currency">{txn.currency}</div>
      </div>
      <div className="case-concept">
        <p className="concept">
          {segments.map((segment, index) =>
            segment.hit ? <mark key={index}>{segment.text}</mark> : <span key={index}>{segment.text}</span>,
          )}
        </p>
        <dl className="case-meta">
          <div><dt>Movimiento</dt><dd className="mono-small">{txn.id}</dd></div>
          <div><dt>Fecha</dt><dd className="num">{txn.date}</dd></div>
          <div><dt>Cuenta</dt><dd className="mono-small">{txn.account}</dd></div>
          {pos !== null && <div><dt>Cola</dt><dd className="num">{formatCount(pos)} de {formatCount(total)}</dd></div>}
        </dl>
        {unmatched.length > 0 && ranges.length === 0 && (
          <p className="footnote">Sin partida con ese número: {unmatched.map((t) => t.text).join(', ')}.</p>
        )}
      </div>
      <div className="case-proposal">
        {decision && <div className="decided-tag">Ya decidido. Enter guarda los cambios.</div>}
        {proposal.length > 0 ? (
          <>
            <span className="label">Propuesta del pipeline</span>
            <div className="proposal-conf num">{confidence === null ? 'Sin confianza' : formatPercent(confidence, 0)}</div>
            {reason && <p className="proposal-reason">{reason}</p>}
          </>
        ) : (
          <>
            <span className="label">Propuesta del pipeline</span>
            <p className="proposal-reason">Este movimiento no tiene propuesta.</p>
          </>
        )}
      </div>
    </header>
  )
}
