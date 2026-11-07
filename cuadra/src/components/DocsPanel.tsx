import { useEffect, useMemo, useState } from 'react'
import type { CaseData, PdfHit } from '../data/caseLoader'
import type { Db, Priority } from '../data/db'
import type { Candidate } from '../domain/candidates'

const CANDIDATES_TO_SEARCH = 6

type PdfSearch = (db: Db, terms: string[], priority?: Priority) => Promise<PdfHit[]>

/** Runs at low priority: documents must never hold back the keyboard. */
export function useCaseDocs(db: Db | null, data: CaseData, candidates: Candidate[], search: PdfSearch): PdfHit[] {
  const [docs, setDocs] = useState<PdfHit[]>([])
  const terms = useMemo(() => {
    const printed = candidates.slice(0, CANDIDATES_TO_SEARCH).map((c) => c.item.printed)
    return [...new Set([...data.tokens.map((t) => t.text), ...printed])].filter(Boolean)
  }, [data.tokens, candidates])
  const key = terms.join('|')

  useEffect(() => {
    if (!db || !terms.length) {
      setDocs([])
      return
    }
    let current = true
    search(db, terms, 'low').then(
      (hits) => {
        if (current) setDocs(hits)
      },
      () => {
        if (current) setDocs([])
      },
    )
    return () => {
      current = false
    }
    // terms is rebuilt on each render of candidates; key holds its content.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [db, key, search])

  return docs
}

type DocsPanelProps = { docs: PdfHit[]; onOpen: (file: string) => void }

export function DocsPanel({ docs, onOpen }: DocsPanelProps) {
  if (!docs.length) return null
  return (
    <section className="docs" aria-label="Documentos relacionados">
      <h2 className="label">Documentos que lo mencionan</h2>
      <ul>
        {docs.map((doc, index) => (
          <li key={`${doc.file}-${doc.page}`}>
            <button className="doc" onClick={() => onOpen(doc.file)}>
              <span className="doc-file mono-small">
                {index === 0 && <kbd>O</kbd>} {doc.file.split('/').at(-1)}, pág. {doc.page}
              </span>
              <span className="doc-snippet">{doc.snippet}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
