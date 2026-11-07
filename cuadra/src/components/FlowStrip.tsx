import { Check, ListChecks, Scale, ShieldCheck, Wand2, type LucideIcon } from 'lucide-react'
import type { QueueView, ViewCounts } from '../data/queue'
import { formatCount } from '../domain/money'
import { useStore } from '../store'

type Node = {
  id: string
  label: string
  value: string
  note: string
  icon: LucideIcon
  view: QueueView | null
}

const ICON_SIZE = 15

/** How the work flows from the bank to the submission. Each step opens its list. */
export function FlowStrip({ counts, withProposal }: { counts: ViewCounts; withProposal: number }) {
  const setFilters = useStore((s) => s.setFilters)
  const go = useStore((s) => s.go)
  const threshold = useStore((s) => s.filters.readyThreshold)

  const nodes: Node[] = [
    { id: 'all', label: 'Movimientos', value: formatCount(counts.totalAll), note: `${formatCount(withProposal)} con propuesta`, icon: Scale, view: 'all' },
    { id: 'review', label: 'Por revisar', value: formatCount(counts.priority), note: `${formatCount(counts.no_proposal)} sin propuesta`, icon: ListChecks, view: 'priority' },
    { id: 'ready', label: 'Listas', value: formatCount(counts.ready), note: `Confianza desde ${Math.round(threshold * 100)} %`, icon: Wand2, view: 'ready' },
    { id: 'done', label: 'Hechas', value: formatCount(counts.done), note: 'Salen como las decidiste', icon: Check, view: 'done' },
    { id: 'send', label: 'Formato', value: 'Validar', note: 'Comprobar el formato oficial', icon: ShieldCheck, view: null },
  ]
  const nextId = counts.priority ? 'review' : counts.ready ? 'ready' : 'send'

  const open = (node: Node) => {
    if (!node.view) {
      go('export')
      return
    }
    go('review')
    void setFilters({ view: node.view })
  }

  return (
    <ol className="flow" aria-label="Del banco a la validación">
      {nodes.map(({ icon: Icon, ...node }) => (
        <li key={node.id} className="flow-step">
          <button
            className={node.id === nextId ? 'flow-node is-next' : 'flow-node'}
            aria-current={node.id === nextId ? 'step' : undefined}
            onClick={() => open({ icon: Icon, ...node })}
          >
            <span className="flow-label"><Icon size={ICON_SIZE} aria-hidden="true" /> {node.label}</span>
            <span className="flow-value num">{node.value}</span>
            <span className="flow-note">{node.note}</span>
          </button>
        </li>
      ))}
    </ol>
  )
}
