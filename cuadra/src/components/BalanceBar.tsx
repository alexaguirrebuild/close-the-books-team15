import type { Balance } from '../domain/balance'
import { Robot, type RobotMood } from './Robot'

const MOOD: Record<Balance['status'], RobotMood> = {
  empty: 'neutral',
  no_item: 'happy',
  balanced: 'happy',
  explained: 'happy',
  short: 'neutral',
  over: 'worried',
  open_gap: 'worried',
  missing_rate: 'worried',
}

const asPercent = (share: number) => `${share * 100}%`

/** As wide as the movement: the invoices fill it, and it closes when they add up. */
export function BalanceBar({ balance }: { balance: Balance }) {
  const { status, title, detail, segments, fill, notch, credit } = balance
  const over = Math.max(fill - notch, 0)
  return (
    <div className="balance" data-status={status}>
      <div className="balance-track" role="img" aria-label={title}>
        <div className="balance-fill" style={{ transform: `scaleX(${fill})` }}>
          {segments.map((segment) => (
            <span
              key={segment.itemId}
              className="balance-segment"
              style={{ '--share': fill ? segment.share / fill : 0 } as React.CSSProperties}
            />
          ))}
        </div>
        {credit > 0 && <span className="balance-credit" style={{ left: asPercent(fill - credit), width: asPercent(credit) }} />}
        {over > 0 && <span className="balance-over" style={{ left: asPercent(notch), width: asPercent(over) }} />}
        <span className="balance-notch" style={{ left: asPercent(notch) }} />
      </div>
      <div className="balance-text" role="status">
        <span className="balance-title">{title}</span>
        {detail && <span className="balance-detail">{detail}</span>}
      </div>
      <span className="balance-robot" aria-hidden="true"><Robot mood={MOOD[status]} size={22} /></span>
    </div>
  )
}
