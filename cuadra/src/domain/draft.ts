import type { TxnAmount } from './allocation'
import { applyException } from './policy'
import type { Allocation, Decision, ExceptionCode, FxRate, Item, ProposalRow, Txn } from './types'
import { isException } from './types'

export type CaseShape = {
  txn: Txn
  proposal: ProposalRow[]
  items: Map<string, Item>
  decision: Decision | null
}

export type Draft = {
  rows: Allocation[]
  /** Only for a line without items; item rows carry their own. */
  exception: ExceptionCode | null
  category: string | null
  origin: 'decision' | 'proposal' | 'empty'
  touched: boolean
}

const UNIDENTIFIED = 'UNIDENTIFIED'

const validException = (value: string | null): ExceptionCode | null => (value && isException(value) ? value : null)

/** The exception the line shows as a whole: the first one, whichever row carries it. */
export function lineException(draft: Pick<Draft, 'rows' | 'exception'>): ExceptionCode | null {
  return draft.rows.find((row) => row.exception)?.exception ?? draft.exception
}

export function initialDraft(data: CaseShape): Draft {
  if (data.decision) {
    return {
      rows: data.decision.allocations,
      exception: data.decision.exception,
      category: data.decision.category,
      origin: 'decision',
      touched: false,
    }
  }
  if (!data.proposal.length) {
    return { rows: [], exception: null, category: null, origin: 'empty', touched: false }
  }
  const rows = data.proposal.flatMap((p) => {
    if (!p.itemId) return []
    const amount = p.amount ?? data.items.get(p.itemId)?.openAmount ?? 0
    return [{ itemId: p.itemId, amount: Math.abs(amount), exception: validException(p.exception) }]
  })
  const noItem = data.proposal.find((p) => !p.itemId)
  return {
    rows,
    exception: rows.length ? null : validException(noItem?.exception ?? null),
    category: rows.length ? null : (noItem?.category ?? null),
    origin: 'proposal',
    touched: false,
  }
}

export function draftToDecision(txnId: string, draft: Draft, at: number): Decision | null {
  if (!draft.rows.length && !draft.exception && !draft.category) return null
  const hasRows = draft.rows.length > 0
  const category = draft.category ?? (draft.exception === UNIDENTIFIED ? UNIDENTIFIED : null)
  return {
    txnId,
    allocations: draft.rows,
    category: hasRows ? null : category,
    exception: hasRows ? null : draft.exception,
    source: draft.origin === 'proposal' && !draft.touched ? 'confirmed' : 'manual',
    at,
  }
}

export function proposalDecision(data: CaseShape, at: number): Decision | null {
  if (!data.proposal.length) return null
  return draftToDecision(data.txn.id, initialDraft(data), at)
}

type ExceptionContext = { items: Map<string, Item>; txn: TxnAmount; rates: FxRate[] }

/** Picking the exception the line already has removes it. */
export function withException(draft: Draft, code: ExceptionCode, context: ExceptionContext): Draft {
  const current = lineException(draft)
  if (code === UNIDENTIFIED) {
    return { ...draft, rows: [], exception: current === code ? null : code, category: current === code ? null : UNIDENTIFIED, touched: true }
  }
  if (!draft.rows.length) return { ...draft, exception: current === code ? null : code, touched: true }
  const rows = applyException({ rows: draft.rows, code: current === code ? null : code, ...context })
  return { ...draft, rows, exception: null, category: null, touched: true }
}

/** For a line where only one invoice carries the exception, like a dispute among several. */
export function withRowException(draft: Draft, itemId: string, code: ExceptionCode, context: ExceptionContext): Draft {
  const current = draft.rows.find((row) => row.itemId === itemId)?.exception
  const rows = applyException({ rows: draft.rows, code: current === code ? null : code, onlyItemId: itemId, ...context })
  return { ...draft, rows, touched: true }
}

/** A line settles items or has a category, never both. */
export function withCategory(draft: Draft, category: string): Draft {
  return { ...draft, rows: [], category, exception: category === UNIDENTIFIED ? UNIDENTIFIED : null, touched: true }
}
