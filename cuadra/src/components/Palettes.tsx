import { useEffect, useMemo, useState } from 'react'
import { EXCEPTION_LABELS, type ExceptionCode } from '../domain/types'

/** Fixed keys, not reordered by suggestion, so muscle memory keeps working. */
const EXCEPTION_KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0']

type ExceptionPaletteProps = {
  codes: readonly ExceptionCode[]
  current: ExceptionCode | null
  suggested: ExceptionCode[]
  onPick: (code: ExceptionCode) => void
  onClose: () => void
}

export function ExceptionPalette({ codes, current, suggested, onPick, onClose }: ExceptionPaletteProps) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      event.stopPropagation()
      if (event.key === 'Escape') onClose()
      const index = EXCEPTION_KEYS.indexOf(event.key)
      if (index >= 0 && codes[index]) {
        event.preventDefault()
        onPick(codes[index])
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [codes, onPick, onClose])

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Elegir excepción" onClick={onClose}>
      <div className="palette" onClick={(event) => event.stopPropagation()}>
        <h2 className="label">Excepción</h2>
        <ul>
          {codes.map((code, index) => (
            <li key={code}>
              <button className={current === code ? 'palette-row is-selected' : 'palette-row'} onClick={() => onPick(code)}>
                <kbd>{EXCEPTION_KEYS[index]}</kbd>
                <span className="palette-code">{code}</span>
                <span className="muted">{EXCEPTION_LABELS[code]}</span>
                {suggested.slice(0, 2).includes(code) && <span className="tag tag-proposal">Probable</span>}
              </button>
            </li>
          ))}
        </ul>
        <p className="footnote"><kbd>Esc</kbd> cerrar. Si eliges la que ya está, se quita.</p>
      </div>
    </div>
  )
}

type CategoryPaletteProps = {
  categories: string[]
  current: string | null
  onPick: (category: string) => void
  onClose: () => void
}

export function CategoryPalette({ categories, current, onPick, onClose }: CategoryPaletteProps) {
  const [term, setTerm] = useState('')
  const [cursor, setCursor] = useState(0)
  const filtered = useMemo(
    () => categories.filter((c) => c.toLowerCase().includes(term.toLowerCase())),
    [categories, term],
  )

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Asignar category" onClick={onClose}>
      <div className="palette" onClick={(event) => event.stopPropagation()}>
        <h2 className="label">Category, solo para filas sin partida</h2>
        {categories.length === 0 ? (
          <p className="muted">Falta masters/categories.csv en la carpeta.</p>
        ) : (
          <>
            <input
              autoFocus
              className="palette-input"
              value={term}
              placeholder="Escribe para filtrar"
              aria-label="Filtrar categories"
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
                if (event.key === 'Enter' && filtered[cursor]) {
                  event.preventDefault()
                  event.nativeEvent.stopImmediatePropagation()
                  onPick(filtered[cursor])
                }
              }}
            />
            <ul className="palette-scroll">
              {filtered.map((category, index) => (
                <li key={category}>
                  <button
                    className={`palette-row${index === cursor ? ' is-cursor' : ''}${current === category ? ' is-selected' : ''}`}
                    onClick={() => onPick(category)}
                  >
                    <span className="palette-code">{category}</span>
                  </button>
                </li>
              ))}
              {filtered.length === 0 && <li className="muted palette-none">Ninguna category contiene ese texto.</li>}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}
