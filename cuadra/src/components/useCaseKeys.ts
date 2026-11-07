import { useEffect } from 'react'
import { useStore } from '../store'
import type { CaseModel } from './useCaseModel'

export const COMBO_KEYS = ['a', 's', 'd']
/** Only moving may repeat while a key is held: a held Enter must never confirm movements unseen. */
const REPEATABLE = ['j', 'k', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright']

/** The keyboard of a movement. Numbers follow what is on screen, so the list and the keys never disagree. */
export function useCaseKeys(model: CaseModel): void {
  useEffect(() => {
    const { overlay, setOverlay, visible, cursor, setCursor, toggle, applyCombo, fifoSwap, setRows, save } = model
    const { markUnidentified, docs, openDoc, pdf, closeDoc, step, setExpanded, showParty, amountRefs } = model

    const onKey = (event: KeyboardEvent) => {
      if (overlay.kind !== 'none' || event.altKey || event.metaKey || event.ctrlKey) return
      const state = useStore.getState()
      if (state.helpOpen || state.bulkOpen) return
      // The next movement is still loading: this screen shows the previous one, so nothing here may decide.
      if (state.benchRid !== model.txn.rid) {
        const key = event.key.toLowerCase()
        if (key === 'j' || key === 'arrowright') void step(1)
        if (key === 'k' || key === 'arrowleft') void step(-1)
        if (key === 'enter') event.preventDefault()
        return
      }
      const target = event.target
      const amounts = [...amountRefs.current.values()]

      if (target instanceof HTMLInputElement && amounts.includes(target)) {
        if (event.key === 'Enter') {
          event.preventDefault()
          if (event.repeat) return
          target.blur()
          save(event.shiftKey)
        } else if (event.key === 'Escape') {
          target.blur()
        } else if (event.key === 'Tab') {
          event.preventDefault()
          const delta = event.shiftKey ? -1 : 1
          amounts[(amounts.indexOf(target) + delta + amounts.length) % amounts.length]?.focus()
        }
        return
      }
      if (target instanceof HTMLElement && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)) return
      if (event.repeat && !REPEATABLE.includes(event.key.toLowerCase())) {
        if (event.key === 'Enter') event.preventDefault()
        return
      }

      if (event.key === 'Tab' && amounts.length) {
        event.preventDefault()
        amounts[0].focus()
        return
      }
      // A button keeps focus after a click; Enter must still mean "confirm", not "press that again".
      if (event.key === 'Enter' && target instanceof HTMLButtonElement && !target.classList.contains('case-save')) {
        target.blur()
      }
      if (event.shiftKey && event.key !== 'Enter' && event.key !== 'Tab') return

      const key = event.key.toLowerCase()
      if (key === 'enter') {
        event.preventDefault()
        save(event.shiftKey)
      } else if (/^[1-9]$/.test(key)) {
        const candidate = visible[Number(key) - 1]
        if (candidate) toggle(candidate.item)
      } else if (key === 'arrowdown') {
        event.preventDefault()
        setCursor(Math.min(cursor + 1, visible.length - 1))
      } else if (key === 'arrowup') {
        event.preventDefault()
        setCursor(Math.max(cursor - 1, 0))
      } else if (key === ' ') {
        event.preventDefault()
        const candidate = visible[cursor]
        if (candidate) toggle(candidate.item)
      } else if (COMBO_KEYS.includes(key)) {
        applyCombo(COMBO_KEYS.indexOf(key))
      } else if (key === 'f' && fifoSwap) {
        setRows([fifoSwap.itemId], [fifoSwap])
      } else if (key === 'e') {
        event.preventDefault()
        setOverlay({ kind: 'exception', itemId: null })
      } else if (key === 'g') {
        event.preventDefault()
        setOverlay({ kind: 'category' })
      } else if (key === 'u') {
        markUnidentified()
      } else if (key === '/') {
        event.preventDefault()
        setOverlay({ kind: 'search' })
      } else if (key === 'm') {
        setExpanded((current) => ({ ...current, party: !showParty }))
      } else if (key === 'o') {
        if (pdf) closeDoc()
        else if (docs[0]) openDoc(docs[0].file)
      } else if (key === 'escape' && pdf) {
        closeDoc()
      } else if (key === 'j' || key === 'arrowright') {
        void step(1)
      } else if (key === 'k' || key === 'arrowleft') {
        void step(-1)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [model])
}
