import { useEffect, useState } from 'react'
import type { CaseData } from '../data/caseLoader'
import { VIEW_LABEL, VIEW_ORDER_IN_TABS } from '../data/queue'
import { formatCount } from '../domain/money'
import { useStore } from '../store'
import { CaseDetail } from './CaseDetail'
import { Robot } from './Robot'

export function CasePane() {
  const benchRid = useStore((s) => s.benchRid)
  const benchPos = useStore((s) => s.benchPos)
  const queueTotal = useStore((s) => s.queueTotal)
  const caseVersion = useStore((s) => s.caseVersion)
  const caseFor = useStore((s) => s.caseFor)
  const [loaded, setLoaded] = useState<{ data: CaseData; version: number } | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  useEffect(() => {
    setFailure(null)
    if (benchRid === null) {
      setLoaded(null)
      return
    }
    let current = true
    caseFor(benchRid).then(
      (next) => {
        if (current) setLoaded({ data: next, version: caseVersion })
      },
      (error: unknown) => {
        if (current) setFailure(error instanceof Error ? error.message : String(error))
      },
    )
    return () => {
      current = false
    }
  }, [benchRid, caseFor, caseVersion])

  if (failure) return <p className="problem page-message">No se pudo abrir el movimiento: {failure}</p>
  if (benchRid === null) return <NothingLeft />
  if (!loaded) return <div className="case-empty"><Robot mood="working" size={48} /></div>
  const { data, version } = loaded
  // The previous movement stays on screen until the next arrives, so nothing flickers between cases.
  return <CaseDetail key={`${data.txn.rid}:${version}`} data={data} pos={benchPos} total={queueTotal} isLoadingNext={data.txn.rid !== benchRid} />
}

function NothingLeft() {
  const counts = useStore((s) => s.counts)
  const filters = useStore((s) => s.filters)
  const setFilters = useStore((s) => s.setFilters)
  const go = useStore((s) => s.go)
  const others = VIEW_ORDER_IN_TABS.filter((view) => view !== filters.view && view !== 'done' && (counts?.[view] ?? 0) > 0)
  return (
    <div className="case-empty">
      <Robot mood="happy" size={64} />
      <h2>Nada más en la vista {VIEW_LABEL[filters.view]}.</h2>
      <div className="case-empty-actions">
        {others.map((view) => (
          <button key={view} className="btn" onClick={() => void setFilters({ view })}>
            {VIEW_LABEL[view]} <span className="num muted">{formatCount(counts?.[view] ?? 0)}</span>
          </button>
        ))}
        <button className="btn btn-primary" onClick={() => go('export')}>Validar el formato</button>
      </div>
    </div>
  )
}
