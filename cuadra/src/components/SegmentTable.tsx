import { useState } from 'react'
import type { SegmentRow } from '../data/diagnosis'
import { DIAGNOSIS_LABEL, type DiagnosisKind } from '../domain/goldenDiff'
import { formatCount, formatPercent } from '../domain/money'
import { useStore } from '../store'

const VISIBLE_SEGMENTS = 12

function mainProblem(row: SegmentRow): string {
  const [kind, count] = (Object.entries(row.kinds) as [DiagnosisKind, number][]).reduce(
    (best, next) => (next[1] > best[1] ? next : best),
    ['ok', 0] as [DiagnosisKind, number],
  )
  return count ? DIAGNOSIS_LABEL[kind] : 'Ninguno'
}

/** Where lines are being lost, worst first. A row opens exactly those errors. */
export function SegmentTable({ rows }: { rows: SegmentRow[] }) {
  const setFilters = useStore((s) => s.setFilters)
  const go = useStore((s) => s.go)
  const [showAll, setShowAll] = useState(false)
  const shown = showAll ? rows : rows.slice(0, VISIBLE_SEGMENTS)

  const open = (segment: string) => {
    go('review')
    void setFilters({ view: 'errors', segment })
  }

  return (
    <>
      <table className="table table-rows segments">
        <thead>
          <tr>
            <th>Segmento</th>
            <th>Acierto</th>
            <th className="right">Perdidas</th>
            <th>Fallo más común</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((row) => (
            <tr key={row.segment} tabIndex={0} onClick={() => open(row.segment)} onKeyDown={(e) => e.key === 'Enter' && open(row.segment)}>
              <td className="id">{row.segment}</td>
              <td className="segment-accuracy" title={`${formatCount(row.correct)} de ${formatCount(row.lines)} líneas`}>
                <span className="bar-track" aria-hidden="true">
                  <span className="bar-fill" style={{ width: `${row.accuracy * 100}%` }} />
                </span>
                <span className="num">{formatPercent(row.accuracy, 0)}</span>
              </td>
              <td className="right num">{formatCount(row.lines - row.correct)}</td>
              <td className="muted">{mainProblem(row)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > VISIBLE_SEGMENTS && (
        <button className="link footnote" onClick={() => setShowAll(!showAll)}>
          {showAll ? 'Ver menos' : `Ver los ${rows.length} segmentos`}
        </button>
      )}
    </>
  )
}
