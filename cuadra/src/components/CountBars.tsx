import { formatCount } from '../domain/money'

export type CountRow = { key: string; label: string; count: number; title?: string }

type CountBarsProps = { rows: CountRow[]; empty: string; limit?: number }

const DEFAULT_LIMIT = 8

/** Ranked counts: one series, one hue, the figure at the end of each bar. */
export function CountBars({ rows, empty, limit = DEFAULT_LIMIT }: CountBarsProps) {
  if (!rows.length) return <p className="muted">{empty}</p>
  const max = Math.max(...rows.map((row) => row.count), 1)
  const shown = rows.slice(0, limit)
  const rest = rows.slice(limit).reduce((sum, row) => sum + row.count, 0)
  return (
    <>
      <ul className="bars">
        {shown.map((row) => (
          <li key={row.key} title={`${row.title ?? row.label}: ${formatCount(row.count)}`}>
            <span className="bar-label">{row.label}</span>
            <span className="bar-track" aria-hidden="true">
              <span className="bar-fill" style={{ width: `${(row.count / max) * 100}%` }} />
            </span>
            <span className="num bar-value">{formatCount(row.count)}</span>
          </li>
        ))}
      </ul>
      {rest > 0 && <p className="footnote">Y otros {formatCount(rest)}.</p>}
    </>
  )
}
