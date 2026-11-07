import { FolderOpen, Gauge, Keyboard, ListChecks, ShieldCheck, type LucideIcon } from 'lucide-react'
import { useStore, type Screen } from '../store'
import { Robot } from './Robot'

const NAV: { screen: Screen; label: string; key: string; icon: LucideIcon }[] = [
  { screen: 'panel', label: 'Resumen', key: '1', icon: Gauge },
  { screen: 'review', label: 'Revisión', key: '2', icon: ListChecks },
  { screen: 'export', label: 'Validar', key: '3', icon: ShieldCheck },
]

const ICON_SIZE = 18

export function AppRail() {
  const screen = useStore((s) => s.screen)
  const go = useStore((s) => s.go)
  const setHelp = useStore((s) => s.setHelp)

  return (
    <nav className="rail" aria-label="Pantallas">
      <div className="rail-brand" title="Cuadra">
        <Robot mood="happy" size={28} />
      </div>
      {NAV.map(({ screen: target, label, key, icon: Icon }) => (
        <button
          key={target}
          className={screen === target ? 'rail-item is-active' : 'rail-item'}
          aria-current={screen === target ? 'page' : undefined}
          onClick={() => go(target)}
          title={`${label} (Alt+${key})`}
        >
          <Icon size={ICON_SIZE} aria-hidden="true" />
          {label}
        </button>
      ))}
      <div className="rail-bottom">
        <button className="rail-item" style={{ width: '100%' }} onClick={() => setHelp(true)} title="Atajos de teclado (?)">
          <Keyboard size={ICON_SIZE} aria-hidden="true" />
          Atajos
        </button>
        <button
          className="rail-item"
          style={{ width: '100%' }}
          onClick={() => window.location.reload()}
          title="Elegir otra carpeta o fase. Tu revisión queda guardada."
        >
          <FolderOpen size={ICON_SIZE} aria-hidden="true" />
          Datos
        </button>
      </div>
    </nav>
  )
}
