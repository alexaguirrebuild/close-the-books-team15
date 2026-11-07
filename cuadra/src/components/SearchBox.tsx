import { useEffect, useState } from 'react'
import { searchItems } from '../data/caseLoader'
import { formatMoney } from '../domain/money'
import type { Item } from '../domain/types'
import { useStore } from '../store'

const DEBOUNCE_MS = 150

type SearchBoxProps = {
  used: Map<string, string>
  onPick: (item: Item) => void
  onClose: () => void
}

export function SearchBox({ used, onPick, onClose }: SearchBoxProps) {
  const db = useStore((s) => s.db)
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
      <div className="palette palette-wide" onClick={(event) => event.stopPropagation()}>
        <h2 className="label">Buscar partidas por id, número impreso, tercero o importe</h2>
        <input
          autoFocus
          className="palette-input"
          value={term}
          placeholder="F-2024/100123, Acme, 1250,00"
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
            if (event.key === 'Enter' && results[cursor]) {
              event.preventDefault()
              event.nativeEvent.stopImmediatePropagation()
              onPick(results[cursor])
            }
          }}
        />
        {failure && <p className="problem">La búsqueda falló: {failure}</p>}
        <ul className="palette-scroll">
          {results.map((item, index) => (
            <li key={item.itemId}>
              <button className={`palette-row search-row${index === cursor ? ' is-cursor' : ''}`} onClick={() => onPick(item)}>
                <span className="mono-small">{item.itemId}</span>
                <span>{item.printed}</span>
                <span className="cand-party">{item.partyName}</span>
                {used.get(item.itemId) && <span className="tag tag-used">Ya en {used.get(item.itemId)}</span>}
                {!item.isOpen && <span className="tag tag-used">No abierta</span>}
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
