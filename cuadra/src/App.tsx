import { useEffect } from 'react'
import { Shell } from './components/Shell'
import { Bench } from './screens/Bench'
import { Dashboard } from './screens/Dashboard'
import { ExportScreen } from './screens/ExportScreen'
import { LoadScreen } from './screens/LoadScreen'
import { QueueScreen } from './screens/QueueScreen'
import { useStore, type Screen } from './store'

const SCREEN_BY_CODE: Record<string, Screen> = {
  Digit1: 'panel',
  Digit2: 'queue',
  Digit3: 'bench',
  Digit4: 'export',
}

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

export function App() {
  const meta = useStore((s) => s.meta)
  const screen = useStore((s) => s.screen)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const state = useStore.getState()
      if (!state.meta) return
      if (event.altKey && SCREEN_BY_CODE[event.code]) {
        event.preventDefault()
        state.go(SCREEN_BY_CODE[event.code])
        return
      }
      if (isTyping(event.target)) return
      if (event.key === '?') {
        event.preventDefault()
        state.setHelp(!state.helpOpen)
      }
      if (event.key === 'Escape' && state.helpOpen) state.setHelp(false)
      if ((event.key === 'z' || event.key === 'Z') && !event.metaKey && !event.ctrlKey) {
        event.preventDefault()
        void state.undoLast()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (!meta) return <LoadScreen />

  return (
    <Shell>
      {screen === 'panel' && <Dashboard />}
      {screen === 'queue' && <QueueScreen />}
      {screen === 'bench' && <Bench />}
      {screen === 'export' && <ExportScreen />}
    </Shell>
  )
}
