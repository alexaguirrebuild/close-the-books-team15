import { useEffect } from 'react'
import { flushSync } from 'react-dom'
import { BulkDialog } from '../components/BulkDialog'
import { CasePane } from '../components/CasePane'
import { QueuePanel, SEARCH_INPUT_ID } from '../components/QueuePanel'
import { availableViews } from '../data/queue'
import { useStore } from '../store'

const PAGE_JUMP = 100

const isModalOpen = () => document.querySelector('[aria-modal="true"]') !== null

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

/** List and case on one screen: moving through the queue never loses the place in it. */
export function Review() {
  const listHidden = useStore((s) => s.listHidden)
  const bulkOpen = useStore((s) => s.bulkOpen)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey || event.metaKey || event.ctrlKey || isModalOpen() || isTyping(event.target)) return
      const state = useStore.getState()
      const key = event.key.toLowerCase()
      if (key === 'l') {
        event.preventDefault()
        state.toggleList()
      } else if (key === 'b') {
        event.preventDefault()
        // Right now, not later: whatever is typed next has to land in the box, not act as shortcuts.
        if (!state.listHidden) {
          document.getElementById(SEARCH_INPUT_ID)?.focus()
        } else {
          flushSync(() => state.toggleList())
          document.getElementById(SEARCH_INPUT_ID)?.focus()
        }
      } else if (key === 'v') {
        event.preventDefault()
        const views = availableViews(state.meta)
        const next = views[(views.indexOf(state.filters.view) + 1) % views.length]
        void state.setFilters({ view: next })
      } else if (key === 'c' && event.shiftKey) {
        event.preventDefault()
        if (state.filters.view === 'ready' && state.counts?.ready) state.setBulkOpen(true)
      } else if (event.key === 'PageDown' || event.key === 'PageUp') {
        event.preventDefault()
        const delta = event.key === 'PageDown' ? PAGE_JUMP : -PAGE_JUMP
        void state.jumpTo(state.cursorPos + delta)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className={listHidden ? 'review is-list-hidden' : 'review'}>
      {!listHidden && <QueuePanel />}
      <CasePane />
      {bulkOpen && <BulkDialog />}
    </div>
  )
}
