import { useEffect, useMemo, useState } from 'react'
import { clearsInFull, type Suggestion } from '../domain/policy'
import { EXCEPTION_LABELS, type ExceptionCode } from '../domain/types'
import { useFocusTrap } from './useFocusTrap'

/** Fixed keys, not reordered by suggestion, so muscle memory keeps working. */
const EXCEPTION_KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0']

const effectOf = (code: ExceptionCode): string => {
  if (code === 'UNIDENTIFIED') return 'Quita las partidas: cobro sin partida.'
  return clearsInFull(code) ? 'La partida se liquida entera por su saldo.' : 'Se aplica lo pagado; la partida sigue abierta.'
}

type ExceptionPaletteProps = {
  title: string
  codes: readonly ExceptionCode[]
  current: ExceptionCode | null
  suggestions: Suggestion[]
  onPick: (code: ExceptionCode) => void
  onClose: () => void
}

export function ExceptionPalette({ title, codes, current, suggestions, onPick, onClose }: ExceptionPaletteProps) {
  const trapRef = useFocusTrap<HTMLDivElement>()
  const [cursor, setCursor] = useState(() => {
    const first = current ?? suggestions[0]?.code
    return Math.max(first ? codes.indexOf(first) : 0, 0)
  })

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      event.stopPropagation()
      // The Enter that opened this palette may still be held: it must not pick a code unseen.
      if (event.repeat && event.key !== 'ArrowDown' && event.key !== 'ArrowUp') {
        event.preventDefault()
        return
      }
      if (event.key === 'Escape') onClose()
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setCursor((c) => Math.min(c + 1, codes.length - 1))
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setCursor((c) => Math.max(c - 1, 0))
      }
      if (event.key === 'Enter') {
        event.preventDefault()
        onPick(codes[cursor])
      }
      const index = EXCEPTION_KEYS.indexOf(event.key)
      if (index >= 0 && codes[index]) {
        event.preventDefault()
        onPick(codes[index])
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [codes, cursor, onPick, onClose])

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <div className="palette" ref={trapRef} tabIndex={-1} onClick={(event) => event.stopPropagation()}>
        <h2 className="palette-title">{title}</h2>
        <ul className="palette-list">
          {codes.map((code, index) => {
            const suggestion = suggestions.find((s) => s.code === code)
            return (
              <li key={code}>
                <button
                  className={`palette-row${index === cursor ? ' is-cursor' : ''}${current === code ? ' is-selected' : ''}`}
                  onClick={() => onPick(code)}
                >
                  <kbd>{EXCEPTION_KEYS[index]}</kbd>
                  <span>
                    <span className="palette-name">{EXCEPTION_LABELS[code]}</span> <span className="palette-code">{code}</span>
                  </span>
                  {suggestion && <span className="pill pill-review">Probable</span>}
                  <span className="palette-why">{suggestion ? suggestion.why : effectOf(code)}</span>
                </button>
              </li>
            )
          })}
        </ul>
        <p className="footnote"><kbd>Enter</kbd> elige la marcada. <kbd>Esc</kbd> cierra. Si eliges la que ya está, se quita.</p>
      </div>
    </div>
  )
}

type CategoryPaletteProps = {
  categories: string[]
  labels: Map<string, string>
  current: string | null
  onPick: (category: string) => void
  onClose: () => void
}

export function CategoryPalette({ categories, labels, current, onPick, onClose }: CategoryPaletteProps) {
  const trapRef = useFocusTrap<HTMLDivElement>()
  const [term, setTerm] = useState('')
  const [cursor, setCursor] = useState(0)
  const filtered = useMemo(() => {
    const needle = term.toLowerCase()
    return categories.filter((c) => `${c} ${labels.get(c) ?? ''}`.toLowerCase().includes(needle))
  }, [categories, labels, term])

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Asignar categoría" onClick={onClose}>
      <div className="palette" ref={trapRef} tabIndex={-1} onClick={(event) => event.stopPropagation()}>
        <h2 className="palette-title">Categoría: este movimiento no liquida ninguna partida</h2>
        {categories.length === 0 ? (
          <p className="muted">Faltan los maestros: categories.csv no está en la carpeta cargada.</p>
        ) : (
          <>
            <input
              autoFocus
              className="palette-input"
              value={term}
              placeholder="Escribe para filtrar"
              aria-label="Filtrar categorías"
              onChange={(event) => {
                setTerm(event.target.value)
                setCursor(0)
              }}
              onKeyDown={(event) => {
                event.stopPropagation()
                if (event.key === 'Escape') onClose()
                if (event.key === 'ArrowDown') {
                  event.preventDefault()
                  setCursor((c) => Math.min(c + 1, filtered.length - 1))
                }
                if (event.key === 'ArrowUp') {
                  event.preventDefault()
                  setCursor((c) => Math.max(c - 1, 0))
                }
                if (event.key === 'Enter' && !event.repeat && filtered[cursor]) {
                  event.preventDefault()
                  event.nativeEvent.stopImmediatePropagation()
                  onPick(filtered[cursor])
                }
              }}
            />
            <ul className="palette-list">
              {filtered.map((category, index) => (
                <li key={category}>
                  <button
                    className={`palette-row${index === cursor ? ' is-cursor' : ''}${current === category ? ' is-selected' : ''}`}
                    style={{ gridTemplateColumns: 'minmax(0, 1fr)' }}
                    onClick={() => onPick(category)}
                  >
                    <span>
                      <span className="palette-name">{labels.get(category) || category}</span> <span className="palette-code">{category}</span>
                    </span>
                  </button>
                </li>
              ))}
              {filtered.length === 0 && <li className="palette-none">Ninguna categoría contiene ese texto.</li>}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}
