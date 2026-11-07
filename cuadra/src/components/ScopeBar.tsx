import { formatCount, formatPercent } from '../domain/money'
import { useStore } from '../store'

const PACE_WINDOW_MS = 5 * 60 * 1000
const MS_PER_MINUTE = 60_000

function SessionPace() {
  const times = useStore((s) => s.sessionTimes)
  const recent = times.filter((t) => Date.now() - t < PACE_WINDOW_MS)
  if (recent.length < 2) return <span className="scope-pace pace">{times.length} en esta sesión</span>
  const minutes = Math.max((Date.now() - recent[0]) / MS_PER_MINUTE, 1 / 60)
  return <span className="scope-pace pace">{times.length} en esta sesión, {Math.round(recent.length / minutes)} por minuto</span>
}

export function ScopeBar() {
  const meta = useStore((s) => s.meta)
  const counts = useStore((s) => s.counts)
  const screen = useStore((s) => s.screen)
  const go = useStore((s) => s.go)
  const total = counts?.totalAll ?? meta?.txnCount ?? 0
  const done = counts?.doneAll ?? 0
  const ratio = total ? done / total : 0

  return (
    <header className="scope">
      {meta?.phase && <span className="scope-phase">{meta.phase.toUpperCase()}</span>}
      <span className="scope-name" title={meta?.name}>{meta?.name}</span>
      <div className="scope-progress" role="status" aria-label={`${formatCount(done)} de ${formatCount(total)} movimientos hechos`}>
        <span className="scope-meter" aria-hidden="true">
          <span style={{ transform: `scaleX(${ratio})` }} />
        </span>
        <span className="num">{formatPercent(ratio, ratio && ratio < 0.1 ? 1 : 0)} hecho</span>
        <span className="muted num">{formatCount(done)} de {formatCount(total)}</span>
      </div>
      <div className="scope-right">
        <SessionPace />
        {screen !== 'export' && (
          <button className="btn btn-primary" onClick={() => go('export')} title="Comprobar el formato oficial (Alt+3)">
            Validar
          </button>
        )}
      </div>
    </header>
  )
}
