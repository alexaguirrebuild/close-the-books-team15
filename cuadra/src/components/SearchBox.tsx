import { useEffect, useState } from 'react'
import { searchItems } from '../data/caseLoader'
import { formatMoney } from '../domain/money'
import type { Item } from '../domain/types'
import { useStore } from '../store'
import { useFocusTrap } from './useFocusTrap'

const DEBOUNCE_MS = 150

type SearchBoxProps = {
  used: Map<string, string>
  onPick: (item: Item) => void
  onClose: () => void
}

export function SearchBox({ used, onPick, onClose }: SearchBoxProps) {
  const db = useStore((s) => s.db)
  const trapRef = useFocusTrap<HTMLDivElement>()
  const [term, setTerm] = useState('')
  const [results, setResults] = useState<Item[]>([])
  const [cursor, setCursor] = useState(0)
  const [failure, setFailure] = useState<string | null>(null)

  useEffect(() => {
    if (!db) return
    let current = true
    const handle = setTimeout(() => {
      searchItems(db, term).then(
        (next) => {
          if (current) {
            setResults(next)
            setCursor(0)
            setFailure(null)
          }
        },
        (error: unknown) => setFailure(error instanceof Error ? error.message : String(error)),
      )
    }, DEBOUNCE_MS)
    return () => {
      current = false
      clearTimeout(handle)
    }
  }, [db, term])

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Buscar partidas" onClick={onClose}>
      <div className="palette palette-wide" ref={trapRef} tabIndex={-1} onClick={(event) => event.stopPropagation()}>
        <h2 className="palette-title">Buscar partida por número, tercero, pedido o importe</h2>
        <input
          autoFocus
          className="palette-input"
          value={term}
          placeholder="FV25-0513218, Riegos Oasis, 1.250,00"
          aria-label="Buscar partidas"
          onChange={(event) => setTerm(event.target.value)}
          onKeyDown={(event) => {
            event.stopPropagation()
            if (event.key === 'Escape') onClose()
            if (event.key === 'ArrowDown') {
              event.preventDefault()
              setCursor((c) => Math.min(c + 1, results.length - 1))
            }
            if (event.key === 'ArrowUp') {
              event.preventDefault()
              setCursor((c) => Math.max(c - 1, 0))
            }
            if (event.key === 'Enter' && !event.repeat && results[cursor]) {
              event.preventDefault()
              event.nativeEvent.stopImmediatePropagation()
              onPick(results[cursor])
            }
          }}
        />
        {failure && <p className="problem">No se pudo buscar: {failure}</p>}
        <ul className="palette-list">
          {results.map((item, index) => (
            <li key={item.itemId}>
              <button className={`palette-row search-row${index === cursor ? ' is-cursor' : ''}`} onClick={() => onPick(item)}>
                <span className="id">{item.printed || item.itemId}</span>
                <span className="cand-party">
                  {item.partyName}
                  {used.get(item.itemId) && <span className="pill pill-review"> Ya en {used.get(item.itemId)}</span>}
                </span>
                <span className="num search-amount">{formatMoney(item.openAmount, item.currency)}</span>
              </button>
            </li>
          ))}
          {term.trim().length >= 2 && results.length === 0 && !failure && (
            <li className="muted palette-none">Ninguna partida coincide con esa búsqueda.</li>
          )}
        </ul>
      </div>
    </div>
  )
}
