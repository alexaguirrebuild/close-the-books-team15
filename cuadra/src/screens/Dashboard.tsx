import { ArrowRight, Download } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { CountBars, type CountRow } from '../components/CountBars'
import { FlowStrip } from '../components/FlowStrip'
import { SegmentTable } from '../components/SegmentTable'
import { exportErrorsCsv, segmentReport, type SegmentRow } from '../data/diagnosis'
import { downloadBytes } from '../data/download'
import { DEFAULT_FILTERS, viewCounts, type ViewCounts } from '../data/queue'
import { loadDashboard, scoreAgainstGolden, type DashboardStats } from '../data/stats'
import { formatCount, formatMoney, formatPercent } from '../domain/money'
import type { ScoreResult } from '../domain/score'
import { EXCEPTION_LABELS, isException } from '../domain/types'
import { useStore } from '../store'

/** The score loads every row into memory: past this size it waits for the button. */
const AUTO_SCORE_MAX_LINES = 300_000

const SCORE_PARTS: { metric: 'allocationF1' | 'transactionAccuracy' | 'exceptionMacroF1'; label: string; weight: string }[] = [
  { metric: 'allocationF1', label: 'Partidas acertadas (F1)', weight: '0,5' },
  { metric: 'transactionAccuracy', label: 'Movimientos enteros bien', weight: '0,3' },
  { metric: 'exceptionMacroF1', label: 'Excepciones (macro-F1)', weight: '0,2' },
]

const decimal = (value: number, digits: number) => value.toFixed(digits).replace('.', ',')

export function Dashboard() {
  const db = useStore((s) => s.db)
  const meta = useStore((s) => s.meta)
  const revision = useStore((s) => s.revision)
  const threshold = useStore((s) => s.filters.readyThreshold)
  const go = useStore((s) => s.go)
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [counts, setCounts] = useState<ViewCounts | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  useEffect(() => {
    if (!db) return
    let current = true
    const load = async () => {
      await useStore.getState().flushWrites()
      const [nextStats, nextCounts] = [await loadDashboard(db), await viewCounts(db, { ...DEFAULT_FILTERS, readyThreshold: threshold })]
      if (current) {
        setStats(nextStats)
        setCounts(nextCounts)
      }
    }
    load().catch((error: unknown) => {
      if (current) setFailure(error instanceof Error ? error.message : String(error))
    })
    return () => {
      current = false
    }
  }, [db, revision, threshold])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Enter' || event.repeat || event.target instanceof HTMLButtonElement || event.target instanceof HTMLTableRowElement) return
      if (document.querySelector('[aria-modal="true"]')) return
      event.preventDefault()
      go('review')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go])

  if (failure) return <p className="problem page-message">No se pudieron calcular las cifras: {failure}</p>
  if (!stats || !counts || !meta) return <div className="page-message muted">Calculando…</div>

  const { coverage } = stats
  const left = counts.priority
  const unanswered = coverage.uncovered

  return (
    <div className="summary">
      <header className="summary-head">
        <div>
          <h1 className="summary-title">
            {left ? <><span className="num">{formatCount(left)}</span> movimientos por revisar</> : 'Nada urgente por revisar'}
          </h1>
          <p className="summary-lede">
            {left
              ? `Empieza por los que más pueden subir la puntuación. Lo que no revises se queda con la propuesta del pipeline${unanswered ? `, y ${formatCount(unanswered)} sin propuesta quedan como no identificados` : ''}.`
              : 'Confirma las propuestas listas o valida el formato.'}
          </p>
        </div>
        <button className="btn btn-primary summary-cta" onClick={() => go(left ? 'review' : 'export')}>
          {left ? 'Seguir revisando' : 'Validar el formato'} <ArrowRight size={16} aria-hidden="true" /> <kbd>Enter</kbd>
        </button>
      </header>

      <FlowStrip counts={counts} withProposal={coverage.total - coverage.uncovered} />

      {meta.warnings.length > 0 && (
        <section className="summary-warnings" aria-label="Avisos de la carga">
          <ul>{meta.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </section>
      )}

      {meta.hasGolden && <DiagnosisSection />}

      <div className="summary-grid">
        <ExceptionsBlock stats={stats} />
        <CategoriesBlock stats={stats} />
        <ConfidenceBlock stats={stats} />
        <section className="summary-block">
          <h2 className="section-title">Importe por divisa</h2>
          <table className="table">
            <thead>
              <tr><th>Divisa</th><th className="right">Decidido</th><th className="right">Pendiente</th></tr>
            </thead>
            <tbody>
              {stats.currencies.map((row) => (
                <tr key={row.currency}>
                  <td>{row.currency}</td>
                  <td className="right num">{formatMoney(row.reconciled)}</td>
                  <td className="right num">{formatMoney(row.pending)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  )
}

function DiagnosisSection() {
  const db = useStore((s) => s.db)
  const meta = useStore((s) => s.meta)
  const revision = useStore((s) => s.revision)
  const diagnosisRevision = useStore((s) => s.diagnosisRevision)
  const refreshDiagnosis = useStore((s) => s.refreshDiagnosis)
  const notify = useStore((s) => s.notify)
  const [score, setScore] = useState<ScoreResult | null>(null)
  const [segments, setSegments] = useState<SegmentRow[]>([])
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const runRef = useRef(0)

  const calculate = useCallback(async () => {
    if (!db) return
    runRef.current += 1
    const run = runRef.current
    setBusy(true)
    setFailure(null)
    try {
      await refreshDiagnosis()
      const report = await segmentReport(db)
      const nextScore = await scoreAgainstGolden(db)
      // An older run must not overwrite the figures of a newer decision.
      if (run !== runRef.current) return
      setSegments(report)
      setScore(nextScore)
    } catch (error) {
      if (run === runRef.current) setFailure(error instanceof Error ? error.message : String(error))
    } finally {
      if (run === runRef.current) setBusy(false)
    }
  }, [db, refreshDiagnosis])

  const auto = (meta?.txnCount ?? 0) <= AUTO_SCORE_MAX_LINES
  useEffect(() => {
    if (auto) void calculate()
  }, [auto, calculate, revision])

  const stale = diagnosisRevision !== revision

  return (
    <section className="diagnosis" aria-label="Diagnóstico con golden">
      <div className="summary-block score-block">
        <h2 className="section-title">Puntuación con lo que saldría ahora</h2>
        {score ? (
          <>
            <div className="score-total num">{decimal(score.total, 2)}</div>
            <table className="table">
              <tbody>
                {SCORE_PARTS.map((part) => (
                  <tr key={part.metric}>
                    <td>{part.label}</td>
                    <td className="right muted">× {part.weight}</td>
                    <td className="right num">{decimal(score[part.metric], 4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : (
          <p className="muted">{busy ? 'Calculando…' : 'Compara lo que exportarías ahora con el golden.'}</p>
        )}
        {(!auto || !score) && (
          <button className="btn score-button" disabled={busy} onClick={() => void calculate()}>
            {busy ? 'Calculando…' : score ? (stale ? 'Recalcular, hay cambios' : 'Recalcular') : 'Calcular puntuación'}
          </button>
        )}
        {failure && <p className="problem">No se pudo puntuar: {failure}</p>}
        <p className="footnote note">Mismo cálculo que el evaluador oficial, comprobado cifra a cifra con <code>bankrec_score.py</code>.</p>
      </div>
      <div className="summary-block segments-block">
        <div className="block-head">
          <h2 className="section-title">Dónde se pierden movimientos</h2>
          {segments.length > 0 && (
            <button
              className="btn"
              onClick={() => {
                if (!db) return
                exportErrorsCsv(db).then(
                  (bytes) => downloadBytes(bytes, `errores_${meta?.phase ?? 'cuadra'}.csv`),
                  (error: unknown) => notify('error', `No se pudo descargar: ${String(error)}`),
                )
              }}
            >
              <Download size={14} aria-hidden="true" /> errores.csv
            </button>
          )}
        </div>
        {segments.length ? <SegmentTable rows={segments} /> : <p className="muted">Aparecerá al calcular la puntuación.</p>}
      </div>
    </section>
  )
}

function ExceptionsBlock({ stats }: { stats: DashboardStats }) {
  const rows: CountRow[] = stats.exceptions.map(({ label, count }) => ({
    key: label,
    label: isException(label) ? EXCEPTION_LABELS[label] : label,
    title: label,
    count,
  }))
  return (
    <section className="summary-block">
      <h2 className="section-title">Por excepción</h2>
      <CountBars rows={rows} empty="Ninguna propuesta ni decisión lleva excepción." limit={10} />
    </section>
  )
}

function CategoriesBlock({ stats }: { stats: DashboardStats }) {
  const labels = useStore((s) => s.meta?.categoryLabels)
  const rows: CountRow[] = stats.categories.map(({ label, count }) => ({ key: label, label: labels?.get(label) || label, title: label, count }))
  return (
    <section className="summary-block">
      <h2 className="section-title">Por categoría</h2>
      <CountBars rows={rows} empty="Aún no hay movimientos con categoría." />
    </section>
  )
}

function ConfidenceBlock({ stats }: { stats: DashboardStats }) {
  const rows: CountRow[] = stats.confidence.map(({ label, count }) => ({ key: label, label, count }))
  const hasAny = rows.some((row) => row.count > 0)
  return (
    <section className="summary-block">
      <h2 className="section-title">Confianza de las propuestas</h2>
      {hasAny ? (
        <>
          <CountBars rows={rows} empty="" />
          {stats.meanConfidence !== null && (
            <p className="footnote">Media {formatPercent(stats.meanConfidence)}. La da el pipeline; no es un acierto medido.</p>
          )}
        </>
      ) : (
        <p className="muted">Estas propuestas no traen confianza. Cada caso muestra la regla que las decidió.</p>
      )}
    </section>
  )
}
