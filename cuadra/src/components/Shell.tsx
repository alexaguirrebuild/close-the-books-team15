import { X } from 'lucide-react'
import type { ReactNode } from 'react'
import { useStore } from '../store'
import { AppRail } from './AppRail'
import { HelpOverlay } from './HelpOverlay'
import { ScopeBar } from './ScopeBar'

export function Shell({ children }: { children: ReactNode }) {
  const notice = useStore((s) => s.notice)
  const helpOpen = useStore((s) => s.helpOpen)
  const undoLast = useStore((s) => s.undoLast)
  const dismissNotice = useStore((s) => s.dismissNotice)

  return (
    <div className="shell">
      <AppRail />
      <div className="workspace">
        <ScopeBar />
        <main className="main">{children}</main>
      </div>
      {notice && (
        <div className={`notice notice-${notice.tone}`} role={notice.tone === 'error' ? 'alert' : 'status'}>
          <span>{notice.text}</span>
          {notice.undoable && (
            <button className="notice-action" onClick={() => void undoLast()}>
              Deshacer <kbd>Z</kbd>
            </button>
          )}
          {notice.tone === 'error' && (
            <button className="notice-action" aria-label="Cerrar el aviso" onClick={dismissNotice}><X size={14} /></button>
          )}
        </div>
      )}
      {helpOpen && <HelpOverlay />}
    </div>
  )
}
