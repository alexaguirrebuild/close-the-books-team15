import type { Allocation, Decision, ExceptionCode, Item, ProposalRow, Txn } from './types'
import { isException } from './types'

export type CaseShape = {
  txn: Txn
  proposal: ProposalRow[]
  items: Map<string, Item>
  decision: Decision | null
}

export type Draft = {
  rows: Allocation[]
  exception: ExceptionCode | null
  category: string | null
  origin: 'decision' | 'proposal' | 'empty'
  touched: boolean
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
    return [{ itemId: p.itemId, amount }]
  })
  const exception = data.proposal.find((p) => p.exception)?.exception ?? null
  return {
    rows,
    exception: exception && isException(exception) ? exception : null,
    category: rows.length ? null : (data.proposal.find((p) => p.category)?.category ?? null),
    origin: 'proposal',
    touched: false,
  }
}

export function draftToDecision(txnId: string, draft: Draft, at: number): Decision | null {
  if (!draft.rows.length && !draft.exception && !draft.category) return null
  return {
    txnId,
    allocations: draft.rows,
    category: draft.rows.length ? null : draft.category,
    exception: draft.exception,
    source: draft.origin === 'proposal' && !draft.touched ? 'confirmed' : 'manual',
    at,
  }
}

export function proposalDecision(data: CaseShape, at: number): Decision | null {
  if (!data.proposal.length) return null
  return draftToDecision(data.txn.id, initialDraft(data), at)
}
