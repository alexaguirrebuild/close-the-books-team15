import { useEffect, useLayoutEffect, useState } from 'react'
import { bulkSummary, type BulkSummary } from '../data/decisions'
import { formatCount, formatPercent } from '../domain/money'
import { useStore } from '../store'
import { useFocusTrap } from './useFocusTrap'

export function BulkDialog() {
  const db = useStore((s) => s.db)
  const threshold = useStore((s) => s.filters.readyThreshold)
  const setBulkOpen = useStore((s) => s.setBulkOpen)
  const bulkConfirm = useStore((s) => s.bulkConfirm)
  const notify = useStore((s) => s.notify)
  const trapRef = useFocusTrap<HTMLDivElement>()
  const [summary, setSummary] = useState<BulkSummary | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  useEffect(() => {
    if (!db) return
    let current = true
    bulkSummary(db, threshold).then(
      (next) => {
        if (current) setSummary(next)
      },
      (error: unknown) => setFailure(error instanceof Error ? error.message : String(error)),
    )
    return () => {
      current = false
    }
  }, [db, threshold])

  const confirm = async () => {
    if (busy || !summary?.lines) return
    setBusy(true)
    try {
      const count = await bulkConfirm(threshold)
      notify('ok', `Confirmados ${formatCount(count)} movimientos.`, true)
      setBulkOpen(false)
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error))
      setBusy(false)
    }
  }

  // Before painting: the moment the figures are visible, Enter has to confirm them.
  useLayoutEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      event.stopPropagation()
      if (event.key === 'Escape') setBulkOpen(false)
      if (event.key === 'Enter') {
        event.preventDefault()
        if (!event.repeat) void confirm()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  })

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Confirmar en lote" onClick={() => setBulkOpen(false)}>
      <div className="dialog" ref={trapRef} tabIndex={-1} onClick={(event) => event.stopPropagation()}>
        <h2>Confirmar las propuestas con confianza desde {formatPercent(threshold, 0)}</h2>
        {summary ? (
          <dl className="dialog-figures">
            <div><dd className="bulk-lines">{formatCount(summary.lines)}</dd><dt>movimientos</dt></div>
            <div><dd>{formatCount(summary.rows)}</dd><dt>filas</dt></div>
            <div><dd>{formatCount(summary.withException)}</dd><dt>con excepción</dt></div>
          </dl>
        ) : (
          <p className="muted">Calculando…</p>
        )}
        <p className="muted">Se puede deshacer con <kbd>Z</kbd>. La confianza la da el pipeline: en TRAIN, compruébala antes con la vista Errores.</p>
        {failure && <p className="problem">No se pudo confirmar el lote: {failure}</p>}
        <div className="dialog-actions">
          <button className="btn" onClick={() => setBulkOpen(false)}>Cancelar <kbd>Esc</kbd></button>
          <button className="btn btn-primary bulk-confirm" disabled={!summary?.lines || busy} onClick={() => void confirm()}>
            {busy ? 'Confirmando…' : `Confirmar ${formatCount(summary?.lines ?? 0)}`} <kbd>Enter</kbd>
          </button>
        </div>
      </div>
    </div>
  )
}
