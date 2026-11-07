import { describe, expect, test } from 'vitest'
import { draftToDecision, initialDraft, proposalDecision, type CaseShape } from './draft'
import type { Item } from './types'

const item = (over: Partial<Item> = {}): Item => ({
  itemId: 'ARI-1',
  kind: 'AR',
  printed: 'F1',
  partyId: 'C1',
  partyName: 'Acme',
  amount: 100,
  openAmount: 100,
  currency: 'EUR',
  dueDate: '2024-01-01',
  isOpen: true,
  terms: '',
  ...over,
})

const base = (over: Partial<CaseShape> = {}): CaseShape => ({
  txn: { rid: 1, id: 'T1', date: '2024-02-01', amount: 100, currency: 'EUR', account: 'A', description: '' },
  proposal: [],
  items: new Map([['ARI-1', item()]]),
  decision: null,
  ...over,
})

const proposalRow = { itemId: 'ARI-1', amount: 100, exception: null, category: null, confidence: 0.9, reason: 'x' }

describe('initialDraft', () => {
  test('starts from the proposal so Enter confirms it', () => {
    const draft = initialDraft(base({ proposal: [proposalRow] }))
    expect(draft.rows).toEqual([{ itemId: 'ARI-1', amount: 100 }])
    expect(draft.origin).toBe('proposal')
  })

  test('prefers an existing decision over the proposal', () => {
    const decision = { txnId: 'T1', allocations: [{ itemId: 'ARI-1', amount: 70 }], category: null, exception: 'PARTIAL' as const, source: 'manual' as const, at: 1 }
    const draft = initialDraft(base({ proposal: [proposalRow], decision }))
    expect(draft.rows[0].amount).toBe(70)
    expect(draft.exception).toBe('PARTIAL')
    expect(draft.origin).toBe('decision')
  })

  test('falls back to the open amount if the proposal has none', () => {
    const draft = initialDraft(base({ proposal: [{ ...proposalRow, amount: null }] }))
    expect(draft.rows[0].amount).toBe(100)
  })

  test('ignores an exception outside the closed list', () => {
    expect(initialDraft(base({ proposal: [{ ...proposalRow, exception: 'NOPE' }] })).exception).toBeNull()
  })

  test('starts empty without a proposal', () => {
    expect(initialDraft(base()).origin).toBe('empty')
  })
})

describe('draftToDecision', () => {
  const draft = { rows: [{ itemId: 'ARI-1', amount: 100 }], exception: null, category: null, origin: 'proposal' as const, touched: false }

  test('an untouched proposal counts as confirmed', () => {
    expect(draftToDecision('T1', draft, 5)?.source).toBe('confirmed')
  })

  test('an edited draft counts as manual', () => {
    expect(draftToDecision('T1', { ...draft, touched: true }, 5)?.source).toBe('manual')
  })

  test('drops the category when the movement has items', () => {
    expect(draftToDecision('T1', { ...draft, category: 'FEES' }, 5)?.category).toBeNull()
  })

  test('is null when nothing was chosen', () => {
    expect(draftToDecision('T1', { ...draft, rows: [] }, 5)).toBeNull()
  })

  test('accepts an exception without items', () => {
    const result = draftToDecision('T1', { ...draft, rows: [], exception: 'RETURNED' }, 5)
    expect(result?.exception).toBe('RETURNED')
  })
})

describe('proposalDecision', () => {
  test('is null without a proposal', () => {
    expect(proposalDecision(base(), 1)).toBeNull()
  })

  test('builds the decision of the proposal', () => {
    expect(proposalDecision(base({ proposal: [proposalRow] }), 1)?.allocations).toEqual([{ itemId: 'ARI-1', amount: 100 }])
  })
})
