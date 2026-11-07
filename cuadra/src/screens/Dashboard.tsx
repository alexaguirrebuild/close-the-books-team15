import { useEffect, useState } from 'react'
import { buildExportRows } from '../data/exportSubmission'
import { loadDashboard, scoreAgainstGolden, type DashboardStats, type LabelCount } from '../data/stats'
import { formatCount, formatMoney, formatPercent } from '../domain/money'
import type { ScoreResult } from '../domain/score'
import { useStore } from '../store'

const SEGMENTS = [
  { key: 'reviewed', label: 'Revisados a mano', className: 'seg-reviewed' },
  { key: 'bulk', label: 'Confirmados en lote', className: 'seg-bulk' },
  { key: 'proposalOnly', label: 'Propuesta sin revisar', className: 'seg-proposal' },
  { key: 'uncovered', label: 'Sin solución', className: 'seg-empty' },
] as const

export function Dashboard() {
  const db = useStore((s) => s.db)
  const meta = useStore((s) => s.meta)
  const revision = useStore((s) => s.revision)
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  useEffect(() => {
    if (!db) return
    let current = true
    useStore.getState().flushWrites().then(() => loadDashboard(db)).then(
      (next) => {
        if (current) setStats(next)
      },
      (error: unknown) => setFailure(error instanceof Error ? error.message : String(error)),
    )
    return () => {
      current = false
    }
  }, [db, revision])

  if (failure) return <p className="problem page">No se pudieron calcular las cifras: {failure}</p>
  if (!stats || !meta) return <div className="page muted">Calculando…</div>

  const { coverage } = stats
  const covered = coverage.total - coverage.uncovered
  const ratio = coverage.total ? covered / coverage.total : 0

  return (
    <div className="dash">
      <section className="dash-hero">
        <div className="dash-figure">
          <div className="hero-number">{formatPercent(ratio)}</div>
          <p className="lede">
            de los movimientos tiene decisión o propuesta. Los {formatCount(coverage.uncovered)} restantes, de{' '}
            {formatCount(coverage.total)}, saldrán como UNIDENTIFIED; un movimiento sin fila contaría como fallo.
          </p>
        </div>
        <div className="coverage">
          <div className="stack" role="img" aria-label={`Cobertura ${formatPercent(ratio)}`}>
            {SEGMENTS.map((segment) => {
              const value = coverage[segment.key]
              if (!value) return null
              return (
                <div
                  key={segment.key}
                  className={`stack-seg ${segment.className}`}
                  style={{ flexGrow: value }}
                  title={`${segment.label}: ${formatCount(value)}`}
                />
              )
            })}
          </div>
          <dl className="legend">
            {SEGMENTS.map((segment) => (
              <div key={segment.key}>
                <dt>
                  <i className={`swatch ${segment.className}`} />
                  {segment.label}
                </dt>
                <dd className="num">{formatCount(coverage[segment.key])}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {meta.warnings.length > 0 && (
        <section className="dash-warnings">
          <h2 className="label">Avisos de la carga</h2>
          <ul>
            {meta.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </section>
      )}

      <div className="dash-grid">
        <section className="dash-cell dash-score">
          {meta.hasGolden ? <ScorePanel /> : <NoGolden stats={stats} />}
        </section>
        <section className="dash-cell">
          <h2 className="label">Importe conciliado frente a pendiente</h2>
          <table className="table">
            <thead>
              <tr>
                <th>Divisa</th>
                <th className="right">Conciliado</th>
                <th className="right">Pendiente</th>
                <th className="right">Conciliado %</th>
              </tr>
            </thead>
            <tbody>
              {stats.currencies.map((row) => (
                <tr key={row.currency}>
                  <td>{row.currency}</td>
                  <td className="right num">{formatMoney(row.reconciled)}</td>
                  <td className="right num">{formatMoney(row.pending)}</td>
                  <td className="right num">{formatPercent(row.total ? row.reconciled / row.total : 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="footnote">Conciliado cuenta lo decidido en la mesa o en lote. Sin mezclar divisas.</p>
        </section>
      </div>
      <div className={meta.hasGolden ? 'dash-grid dash-grid-3' : 'dash-grid'}>
        <section className="dash-cell">
          <h2 className="label">Por excepción</h2>
          <CountBars rows={stats.exceptions} empty="Ninguna propuesta ni decisión lleva excepción." />
        </section>
        <section className="dash-cell">
          <h2 className="label">Por category</h2>
          <CountBars rows={stats.categories} empty="Aún no hay movimientos con category." />
        </section>
        {meta.hasGolden && (
          <section className="dash-cell">
            <h2 className="label">Confianza de las propuestas</h2>
            <ConfidenceBars stats={stats} />
          </section>
        )}
      </div>
    </div>
  )
}

function CountBars({ rows, empty }: { rows: LabelCount[]; empty: string }) {
  if (!rows.length) return <p className="muted">{empty}</p>
  const max = Math.max(...rows.map((r) => r.count))
  return (
    <ul className="bars">
      {rows.map((row) => (
        <li key={row.label} title={`${row.label}: ${formatCount(row.count)}`}>
          <span className="bar-label">{row.label}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: `${(row.count / max) * 100}%` }} />
          </span>
          <span className="num bar-value">{formatCount(row.count)}</span>
        </li>
      ))}
    </ul>
  )
}

function ConfidenceBars({ stats }: { stats: DashboardStats }) {
  return (
    <>
      <CountBars rows={stats.confidence} empty="No hay propuestas con confianza." />
      {stats.meanConfidence !== null && (
        <p className="footnote">Confianza media {formatPercent(stats.meanConfidence)}. La da el pipeline; no es un acierto medido.</p>
      )}
    </>
  )
}

function NoGolden({ stats }: { stats: DashboardStats }) {
  return (
    <>
      <h2 className="label">Confianza de las propuestas</h2>
      <ConfidenceBars stats={stats} />
      <p className="note">Esta carpeta no trae golden, así que aquí no hay puntuación: solo cobertura y confianza.</p>
    </>
  )
}

const SCORE_PARTS: { key: keyof ScoreResult; label: string; weight: string }[] = [
  { key: 'allocationF1', label: 'Allocation F1', weight: '0,5' },
  { key: 'transactionAccuracy', label: 'Transaction accuracy', weight: '0,3' },
  { key: 'exceptionMacroF1', label: 'Exception macro-F1', weight: '0,2' },
]

function ScorePanel() {
  const db = useStore((s) => s.db)
  const options = useStore((s) => s.exportOptions)
  const revision = useStore((s) => s.revision)
  const [score, setScore] = useState<ScoreResult | null>(null)
  const [scoredRevision, setScoredRevision] = useState(0)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const calculate = async () => {
    if (!db) return
    setBusy(true)
    setFailure(null)
    try {
      await useStore.getState().flushWrites()
      await buildExportRows(db, options)
      setScore(await scoreAgainstGolden(db))
      setScoredRevision(revision)
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h2 className="label">Puntuación contra el golden</h2>
      {score ? (
        <>
          <div className="score-total">{score.total.toFixed(4).replace('.', ',')}</div>
          <table className="table">
            <tbody>
              {SCORE_PARTS.map((part) => (
                <tr key={part.key}>
                  <td>{part.label}</td>
                  <td className="right muted">× {part.weight}</td>
                  <td className="right num">{(score[part.key] as number).toFixed(4).replace('.', ',')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <p className="muted">Compara lo que exportarías ahora con golden.csv.</p>
      )}
      <button className="btn" disabled={busy} onClick={() => void calculate()}>
        {busy ? 'Calculando…' : score ? (scoredRevision === revision ? 'Recalcular' : 'Recalcular, hay cambios') : 'Calcular puntuación'}
      </button>
      {failure && <p className="problem">No se pudo puntuar: {failure}</p>}
      <p className="note">
        Cálculo local de la fórmula publicada, con tolerancia max(0,01; 0,5 %). No es el scorer oficial: contrasta con él
        el submission.csv antes de fiarte de la cifra.
      </p>
    </>
  )
}
