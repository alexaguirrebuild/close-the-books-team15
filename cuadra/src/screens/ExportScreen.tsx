import { ArrowDownToLine, Check, TriangleAlert, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { exportReviewCsv } from '../data/decisions'
import { downloadBytes } from '../data/download'
import { DEFAULT_FILTERS, findTxn, queuePosition } from '../data/queue'
import {
  buildExportRows,
  canDownloadSubmission,
  minConfidenceFromPercent,
  previewExport,
  SUBMISSION_HEADER,
  validateExport,
  writeSubmission,
  type ExportSummary,
  type ValidationIssue,
} from '../data/exportSubmission'
import { formatCount } from '../domain/money'
import { useStore } from '../store'

async function gzip(bytes: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(new CompressionStream('gzip'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

const PREVIEW_ROWS = 10
const BYTES_PER_MB = 1024 * 1024
const ORIGIN_LABEL: Record<string, string> = { review: 'Revisado', proposal: 'Propuesta', auto: 'Automático' }

const formatMegabytes = (bytes: number) => `${(bytes / BYTES_PER_MB).toFixed(1).replace('.', ',')} MB de 60`

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
  const openCase = useStore((s) => s.openCase)
  const filters = useStore((s) => s.filters)
  const setFilters = useStore((s) => s.setFilters)
  const [summary, setSummary] = useState<ExportSummary | null>(null)
  const [issues, setIssues] = useState<ValidationIssue[]>([])
  const [preview, setPreview] = useState<Record<string, string>[]>([])
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
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
  const offersFile = canDownloadSubmission(meta?.phase ?? null)
  const canDownload = offersFile && Boolean(summary) && !errors.length && !busy && !failure
  const fileBase = `submission_${meta?.phase ?? 'cuadra'}`

  const save = async (compressed: boolean) => {
    if (!db || !offersFile) return
    try {
      await useStore.getState().flushWrites()
      const bytes = await writeSubmission(db)
      const firstLine = new TextDecoder().decode(bytes.slice(0, SUBMISSION_HEADER.length))
      if (firstLine !== SUBMISSION_HEADER) throw new Error('La cabecera generada no coincide con la oficial')
      const name = `${fileBase}.csv${compressed ? '.gz' : ''}`
      if (compressed) downloadBytes(await gzip(bytes), name, 'application/gzip')
      else downloadBytes(bytes, name)
      notify('ok', `${name} descargado con ${formatCount(summary?.rows ?? 0)} filas.`)
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
    // Outside the current view, opening it would lose the place: J would jump to the top of the list.
    let position = await queuePosition(db, rid)
    if (position === null) {
      await setFilters({ ...DEFAULT_FILTERS, readyThreshold: filters.readyThreshold, view: 'all' })
      position = await queuePosition(db, rid)
    }
    openCase(rid, position)
  }

  const ctaLabel = busy
    ? 'Validando…'
    : failure
      ? 'No se pudo validar'
      : errors.length
        ? errorsLabel(errors.reduce((n, e) => n + e.count, 0))
        : `Descargar ${fileBase}.csv.gz`

  return (
    <div className="send">
      <section className="send-side">
        <h1 className="send-title">Validar</h1>
        <p className="muted">Cada movimiento queda con lo revisado, si lo hay. Si no, con su propuesta. Si tampoco, como no identificado.</p>

        {summary && (
          <dl className="send-summary">
            <div><dt>Revisados</dt><dd className="num from-review">{formatCount(summary.fromReview)}</dd></div>
            <div><dt>Con propuesta sin revisar</dt><dd className="num">{formatCount(summary.fromProposals)}</dd></div>
            <div className={summary.autoUnidentified ? 'is-attention' : ''}>
              <dt>No identificados automáticos</dt><dd className="num">{formatCount(summary.autoUnidentified)}</dd>
            </div>
            <div><dt>Filas</dt><dd className="num">{formatCount(summary.rows)}</dd></div>
            <div><dt>Tamaño sin comprimir</dt><dd className="num">{formatMegabytes(summary.bytes)}</dd></div>
          </dl>
        )}

        <fieldset className="send-options">
          <label className="check">
            <input type="checkbox" checked={options.includeProposals} onChange={(e) => setOptions({ includeProposals: e.target.checked })} />
            Incluir las propuestas sin revisar
          </label>
          {meta?.hasConfidence && (
            <label className="inline-number">
              Solo con confianza desde
              <input
                type="number"
                min={0}
                max={100}
                disabled={!options.includeProposals}
                value={Math.round(options.minConfidence * 100)}
                onChange={(e) => {
                  const minConfidence = minConfidenceFromPercent(e.target.value)
                  if (minConfidence !== null) setOptions({ minConfidence })
                }}
              />
              %
            </label>
          )}
        </fieldset>

        {offersFile ? (
          <>
            <button className="btn btn-primary btn-large" disabled={!canDownload} onClick={() => void save(true)}>
              {ctaLabel}
              <ArrowDownToLine size={16} aria-hidden="true" />
            </button>
            <button className="link footnote" disabled={!canDownload} onClick={() => void save(false)}>
              Descargarlo sin comprimir
            </button>
          </>
        ) : (
          <p className="review-only">
            Aquí no se genera el fichero de envío: el de esta fase sale del motor del equipo. Sirve para revisar y comprobar el formato; lo decidido se pasa con «Descargar mi revisión».
          </p>
        )}

        <TeamBlock />
      </section>

      <section className="send-main">
        {failure && <p className="problem">No se pudo validar: {failure}</p>}
        <h2 className="section-title">Comprobaciones del formato oficial</h2>
        {summary && !issues.length && !busy && !failure && (
          <p className="valid"><Check size={16} aria-hidden="true" /> Todo correcto. El fichero está listo.</p>
        )}
        {summary && !errors.length && warnings.length > 0 && !failure && (
          <p className="valid"><Check size={16} aria-hidden="true" /> Sin errores. Los avisos no impiden descargar.</p>
        )}
        <div className="issues">
          {[...errors, ...warnings].map((issue) => (
            <details key={issue.code} className={`issue issue-${issue.severity}`} open={issue.severity === 'error'}>
              <summary>
                {issue.severity === 'error' ? <X size={14} aria-label="Error" /> : <TriangleAlert size={14} aria-label="Aviso" />}
                <span className="issue-title">{issue.title}</span>
                <span className="num">{formatCount(issue.count)}</span>
              </summary>
              <table className="table">
                <thead>
                  <tr><th className="right">Fila</th><th>Movimiento</th><th>Detalle</th><th /></tr>
                </thead>
                <tbody>
                  {issue.samples.map((sample) => (
                    <tr key={`${sample.line}-${sample.txn}-${sample.detail}`}>
                      <td className="right num">{sample.line || 'Sin fila'}</td>
                      <td className="id">{sample.txn}</td>
                      <td className="id">{sample.detail}</td>
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

        <h2 className="section-title send-preview-title">Primeras filas del fichero</h2>
        <div className="table-scroll-x">
          <table className="table">
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
                  <td className="id">{row.txn_id}</td>
                  <td className="id">{row.item_id}</td>
                  <td className="right num">{row.amount}</td>
                  <td className="id">{row.category}</td>
                  <td className="id">{row.exception}</td>
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

function TeamBlock() {
  const db = useStore((s) => s.db)
  const notify = useStore((s) => s.notify)
  const importReview = useStore((s) => s.importReview)
  const fileRef = useRef<HTMLInputElement>(null)

  return (
    <div className="send-team">
      <h2 className="section-title">Revisar en equipo</h2>
      <p className="footnote">Cada persona descarga su revisión y otra la importa. Lo ya decidido aquí no se pisa.</p>
      <div className="case-empty-actions send-team-actions">
        <button
          className="btn"
          onClick={() => {
            if (!db) return
            exportReviewCsv(db).then(
              (bytes) => downloadBytes(bytes, 'revision.csv'),
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
  )
}
