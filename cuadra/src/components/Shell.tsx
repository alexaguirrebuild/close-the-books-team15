import { ArrowUpRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { useStore, type Screen } from '../store'
import { HelpOverlay } from './HelpOverlay'
import { Robot } from './Robot'

const NAV: { screen: Screen; label: string; key: string }[] = [
  { screen: 'panel', label: 'Panel', key: '1' },
  { screen: 'queue', label: 'Cola', key: '2' },
  { screen: 'bench', label: 'Mesa', key: '3' },
]

export function Shell({ children }: { children: ReactNode }) {
  const screen = useStore((s) => s.screen)
  const go = useStore((s) => s.go)
  const meta = useStore((s) => s.meta)
  const notice = useStore((s) => s.notice)
  const helpOpen = useStore((s) => s.helpOpen)
  const setHelp = useStore((s) => s.setHelp)

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <Robot mood="happy" size={24} />
          <span>Cuadra</span>
        </div>
        <nav className="nav" aria-label="Pantallas">
          {NAV.map((item) => (
            <button
              key={item.screen}
              className={screen === item.screen ? 'nav-item is-active' : 'nav-item'}
              aria-current={screen === item.screen ? 'page' : undefined}
              onClick={() => go(item.screen)}
              title={`Alt+${item.key}`}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="topbar-right">
          <span className="dataset" title={meta?.name}>
            {meta?.name}
          </span>
          <button className="nav-item" onClick={() => setHelp(true)} title="Atajos de teclado (?)">
            Atajos
          </button>
          <button
            className={screen === 'export' ? 'cta is-active' : 'cta'}
            onClick={() => go('export')}
            title="Alt+4"
          >
            Exportar
            <span className="cta-box">
              <ArrowUpRight size={12} strokeWidth={1.5} />
            </span>
          </button>
        </div>
      </header>
      <main className="main">{children}</main>
      {notice && (
        <div className={`notice notice-${notice.tone}`} role={notice.tone === 'error' ? 'alert' : 'status'}>
          {notice.text}
        </div>
      )}
      {helpOpen && <HelpOverlay />}
    </div>
  )
}
