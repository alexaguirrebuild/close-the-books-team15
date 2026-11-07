import { useEffect, useMemo, useState } from 'react'
import type { CaseData, PdfHit } from '../data/caseLoader'
import type { Db, Priority } from '../data/db'
import type { Candidate } from '../domain/candidates'

const CANDIDATES_TO_SEARCH = 6
/** Shorter pieces of the bank text, like a prefix or a year, appear in every document. */
const MIN_CONCEPT_TERM = 6

type PdfSearch = (db: Db, terms: string[], priority?: Priority) => Promise<PdfHit[]>

/** Runs at low priority: documents must never hold back the keyboard. */
export function useCaseDocs(db: Db | null, data: CaseData, candidates: Candidate[], search: PdfSearch): PdfHit[] {
  const [docs, setDocs] = useState<PdfHit[]>([])
  const terms = useMemo(() => {
    const printed = candidates.slice(0, CANDIDATES_TO_SEARCH).map((c) => c.item.printed)
    const fromConcept = data.tokens.filter((t) => t.norm.length >= MIN_CONCEPT_TERM).map((t) => t.text)
    return [...new Set([...fromConcept, ...printed])].filter(Boolean)
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
