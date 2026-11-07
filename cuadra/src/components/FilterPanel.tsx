import { useEffect, useRef } from 'react'
import { CASE_TYPE_LABEL, type CaseType, type QueueSort } from '../data/queue'
import { useStore } from '../store'

const SORTS: { value: QueueSort; label: string }[] = [
  { value: 'auto', label: 'Según la vista' },
  { value: 'amount', label: 'Mayor importe en EUR' },
  { value: 'confidence_low', label: 'Menor confianza' },
  { value: 'confidence_high', label: 'Mayor confianza' },
]
const NEEDS_CONFIDENCE: QueueSort[] = ['confidence_low', 'confidence_high']

type FilterPanelProps = { onClose: () => void }

export function FilterPanel({ onClose }: FilterPanelProps) {
  const meta = useStore((s) => s.meta)
  const filters = useStore((s) => s.filters)
  const setFilters = useStore((s) => s.setFilters)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    ref.current?.querySelector('select')?.focus()
    // While it is open the keyboard belongs to the panel: Enter here must not confirm the movement behind it.
    const onKey = (event: KeyboardEvent) => {
      event.stopPropagation()
      if (event.key === 'Escape') onClose()
    }
    const onPointer = (event: PointerEvent) => {
      const within = event.target instanceof Node && ref.current?.parentElement?.contains(event.target)
      if (!within) onClose()
    }
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('pointerdown', onPointer)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('pointerdown', onPointer)
    }
  }, [onClose])

  /** A select keeps the keyboard after a change: give it back to the list. */
  const apply = (next: Parameters<typeof setFilters>[0], target: HTMLSelectElement) => {
    target.blur()
    void setFilters(next)
  }

  return (
    <div className="filter-panel" ref={ref}>
      <label>
        Divisa
        <select value={filters.currency} onChange={(e) => apply({ currency: e.target.value }, e.target)}>
          <option value="">Todas</option>
          {meta?.currencies.map((c) => <option key={c}>{c}</option>)}
        </select>
      </label>
      <label>
        Sociedad
        <select value={filters.entity} onChange={(e) => apply({ entity: e.target.value }, e.target)}>
          <option value="">Todas</option>
          {meta?.entities.map((c) => <option key={c}>{c}</option>)}
        </select>
      </label>
      <label>
        Cuenta
        <select value={filters.account} onChange={(e) => apply({ account: e.target.value }, e.target)}>
          <option value="">Todas</option>
          {meta?.accounts.map((a) => <option key={a}>{a}</option>)}
        </select>
      </label>
      <label>
        Tipo de caso
        <select value={filters.caseType} onChange={(e) => apply({ caseType: e.target.value as CaseType | '' }, e.target)}>
          <option value="">Todos</option>
          {Object.entries(CASE_TYPE_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label>
        Ordenar por
        <select value={filters.sort} onChange={(e) => apply({ sort: e.target.value as QueueSort }, e.target)}>
          {SORTS.filter((s) => meta?.hasConfidence || !NEEDS_CONFIDENCE.includes(s.value)).map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
      </label>
    </div>
  )
}
