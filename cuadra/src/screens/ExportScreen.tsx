import { ArrowDownToLine, Check, TriangleAlert, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { exportReviewCsv } from '../data/decisions'
import { findTxn, queuePosition } from '../data/queue'
import {
  buildExportRows,
  previewExport,
  SUBMISSION_HEADER,
  validateExport,
  writeSubmission,
  type ExportSummary,
  type ValidationIssue,
} from '../data/exportSubmission'
import { formatCount, formatPercent } from '../domain/money'
import { useStore } from '../store'

function download(bytes: Uint8Array, name: string) {
  const url = URL.createObjectURL(new Blob([bytes as BlobPart], { type: 'text/csv' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
}

const PREVIEW_ROWS = 10

function errorsLabel(count: number): string {
  return count === 1 ? 'Corrige 1 error' : `Corrige ${formatCount(count)} errores`
}

export function ExportScreen() {
  const db = useStore((s) => s.db)
  const meta = useStore((s) => s.meta)
  const options = useStore((s) => s.exportOptions)
  const setOptions = useStore((s) => s.setExportOptions)
  const revision = useStore((s) => s.revision)
  const notify = useStore((s) => s.notify)
  const importReview = useStore((s) => s.importReview)
  const openCase = useStore((s) => s.openCase)
  const [summary, setSummary] = useState<ExportSummary | null>(null)
  const [issues, setIssues] = useState<ValidationIssue[]>([])
  const [preview, setPreview] = useState<Record<string, string>[]>([])
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const runRef = useRef(0)

  const validate = useCallback(async () => {
    if (!db || !meta) return
    runRef.current += 1
    const run = runRef.current
    setBusy(true)
    setFailure(null)
    try {
      await useStore.getState().flushWrites()
      const nextSummary = await buildExportRows(db, options)
      const nextIssues = await validateExport(db, meta.categories)
      const nextPreview = await previewExport(db, PREVIEW_ROWS)
      if (run !== runRef.current) return
      setSummary(nextSummary)
      setIssues(nextIssues)
      setPreview(nextPreview)
    } catch (error) {
      if (run === runRef.current) setFailure(error instanceof Error ? error.message : String(error))
    } finally {
      if (run === runRef.current) setBusy(false)
    }
  }, [db, meta, options])

  useEffect(() => {
    void validate()
  }, [validate, revision])

  const errors = issues.filter((i) => i.severity === 'error')
  const warnings = issues.filter((i) => i.severity === 'warning')
  const coverage = summary && meta ? summary.txns / meta.txnCount : 0
  const canDownload = Boolean(summary) && !errors.length && !busy && !failure

  const save = async () => {
    if (!db) return
    try {
      await useStore.getState().flushWrites()
      const bytes = await writeSubmission(db)
      const firstLine = new TextDecoder().decode(bytes.slice(0, SUBMISSION_HEADER.length))
      if (firstLine !== SUBMISSION_HEADER) throw new Error('La cabecera generada no coincide con la oficial')
      download(bytes, 'submission.csv')
      notify('ok', `submission.csv descargado con ${formatCount(summary?.rows ?? 0)} filas.`)
    } catch (error) {
      notify('error', `No se pudo generar el CSV: ${error instanceof Error ? error.message : String(error)}`)
    }
  }

  const goToTxn = async (txnId: string) => {
    if (!db) return
    const rid = await findTxn(db, txnId)
    if (rid === null) {
      notify('error', `${txnId} no existe en bank_transactions.csv.`)
      return
    }
    openCase(rid, await queuePosition(db, rid))
  }

  return (
    <div className="export">
      <section className="export-side">
        <h1 className="headline">Exportar el envío</h1>
        <p className="muted">Cada movimiento sale con lo revisado, si lo hay. Si no, con su propuesta. Si tampoco, como UNIDENTIFIED.</p>

        <fieldset className="options">
          <legend className="label">Qué hacer con las propuestas sin revisar</legend>
          <label className="check">
            <input type="checkbox" checked={options.includeProposals} onChange={(e) => setOptions({ includeProposals: e.target.checked })} />
            Incluirlas en el CSV
          </label>
          <label className="inline-number">
            Solo con confianza desde
            <input
              type="number"
              min={0}
              max={100}
              disabled={!options.includeProposals}
              value={Math.round(options.minConfidence * 100)}
              onChange={(e) => setOptions({ minConfidence: Number(e.target.value) / 100 })}
            />
            %
          </label>
        </fieldset>

        {summary && (
          <dl className="summary">
            <div><dt>Filas</dt><dd className="num">{formatCount(summary.rows)}</dd></div>
            <div><dt>Revisados</dt><dd className="num">{formatCount(summary.fromReview)}</dd></div>
            <div><dt>Con propuesta</dt><dd className="num">{formatCount(summary.fromProposals)}</dd></div>
            <div><dt>UNIDENTIFIED automático</dt><dd className="num">{formatCount(summary.autoUnidentified)}</dd></div>
            <div><dt>Cobertura</dt><dd className="num">{formatPercent(coverage)}</dd></div>
          </dl>
        )}

        <button className="cta cta-large cta-wide" disabled={!canDownload} onClick={() => void save()}>
          {busy ? 'Validando…' : failure ? 'No se pudo validar' : errors.length ? errorsLabel(errors.reduce((n, e) => n + e.count, 0)) : 'Descargar submission.csv'}
          <span className="cta-box"><ArrowDownToLine size={14} strokeWidth={1.5} /></span>
        </button>

        <div className="team">
          <h2 className="label">Reparto en el equipo</h2>
          <p className="muted">Cada persona descarga lo suyo y otra lo importa. Lo ya decidido aquí no se pisa.</p>
          <div className="team-actions">
            <button
              className="btn"
              onClick={() => {
                if (!db) return
                exportReviewCsv(db).then(
                  (bytes) => download(bytes, 'revision.csv'),
                  (error: unknown) => notify('error', `No se pudo descargar la revisión: ${String(error)}`),
                )
              }}
            >
              Descargar mi revisión
            </button>
            <button className="btn" onClick={() => fileRef.current?.click()}>Importar revisión</button>
            <input
              ref={fileRef}
              type="file"
              accept=".csv"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0]
                event.target.value = ''
                if (!file) return
                file.text().then(importReview).then(
                  (message) => notify('ok', message),
                  (error: unknown) => notify('error', `No se pudo importar: ${String(error)}`),
                )
              }}
            />
          </div>
        </div>
      </section>

      <section className="export-main">
        {failure && <p className="problem">No se pudo validar: {failure}</p>}
        <h2 className="label">Validación</h2>
        {summary && !issues.length && !busy && !failure && (
          <p className="valid"><Check size={16} /> Sin errores ni avisos. El CSV está listo.</p>
        )}
        {summary && !errors.length && warnings.length > 0 && !failure && (
          <p className="valid"><Check size={16} /> Sin errores. Los avisos no impiden descargar.</p>
        )}
        <div className="issues">
          {[...errors, ...warnings].map((issue) => (
            <details key={issue.code} className={`issue issue-${issue.severity}`} open={issue.severity === 'error'}>
              <summary>
                {issue.severity === 'error' ? <X size={14} aria-label="Error" /> : <TriangleAlert size={14} aria-label="Aviso" />}
                <span className="issue-title">{issue.title}</span>
                <span className="num">{formatCount(issue.count)}</span>
              </summary>
              <table className="table table-compact">
                <thead>
                  <tr><th className="right">Fila</th><th>Movimiento</th><th>Detalle</th><th /></tr>
                </thead>
                <tbody>
                  {issue.samples.map((sample) => (
                    <tr key={`${sample.line}-${sample.txn}-${sample.detail}`}>
                      <td className="right num">{sample.line || 'Sin fila'}</td>
                      <td className="mono-small">{sample.txn}</td>
                      <td className="mono-small">{sample.detail}</td>
                      <td className="right"><button className="link" onClick={() => void goToTxn(sample.txn)}>Abrir</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {issue.count > issue.samples.length && (
                <p className="footnote">Se muestran las primeras {issue.samples.length} de {formatCount(issue.count)}.</p>
              )}
            </details>
          ))}
        </div>

        <h2 className="label label-spaced">Primeras filas</h2>
        <div className="table-scroll-x">
          <table className="table table-compact">
            <thead>
              <tr>
                <th className="right">Fila</th>
                <th>bank_txn_id</th><th>item_id</th><th className="right">amount</th><th>category</th><th>exception</th><th>Origen</th>
              </tr>
            </thead>
            <tbody>
              {preview.map((row) => (
                <tr key={row.line}>
                  <td className="right num">{row.line}</td>
                  <td className="mono-small">{row.txn_id}</td>
                  <td className="mono-small">{row.item_id}</td>
                  <td className="right num">{row.amount}</td>
                  <td className="mono-small">{row.category}</td>
                  <td className="mono-small">{row.exception}</td>
                  <td className="muted">{ORIGIN_LABEL[row.origin] ?? row.origin}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

const ORIGIN_LABEL: Record<string, string> = { review: 'Revisado', proposal: 'Propuesta', auto: 'Automático' }
