import { describe, expect, test } from 'vitest'
import { draftToDecision, initialDraft, lineException, proposalDecision, withCategory, withException, withRowException, type CaseShape } from './draft'
import type { Item, ProposalRow, Txn } from './types'

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
  issueDate: '2023-12-01',
  isOpen: true,
  terms: '',
  po: '',
  ...over,
})

const txn: Txn = {
  rid: 1,
  id: 'T1',
  date: '2024-02-01',
  amount: 100,
  currency: 'EUR',
  account: 'A',
  description: '',
  entity: '',
  bank: '',
  counterparty: '',
  counterpartyAccount: '',
  endToEnd: '',
  remittance: '',
  ref1: '',
  ref2: '',
  origAmount: null,
  origCurrency: '',
}

const base = (over: Partial<CaseShape> = {}): CaseShape => ({
  txn,
  proposal: [],
  items: new Map([['ARI-1', item()]]),
  decision: null,
  ...over,
})

const proposalRow: ProposalRow = {
  itemId: 'ARI-1',
  amount: 100,
  exception: null,
  category: null,
  confidence: 0.9,
  reason: 'x',
  source: '',
  adviceId: null,
  lineNo: null,
}

describe('initialDraft', () => {
  test('starts from the proposal so Enter confirms it', () => {
    const draft = initialDraft(base({ proposal: [proposalRow] }))
    expect(draft.rows).toEqual([{ itemId: 'ARI-1', amount: 100, exception: null }])
    expect(draft.origin).toBe('proposal')
  })

  test('prefers an existing decision over the proposal', () => {
    const decision = {
      txnId: 'T1',
      allocations: [{ itemId: 'ARI-1', amount: 70, exception: 'PARTIAL' as const }],
      category: null,
      exception: null,
      source: 'manual' as const,
      at: 1,
    }
    const draft = initialDraft(base({ proposal: [proposalRow], decision }))
    expect(draft.rows[0].amount).toBe(70)
    expect(lineException(draft)).toBe('PARTIAL')
    expect(draft.origin).toBe('decision')
  })

  test('keeps the exception the proposal gives to each row', () => {
    const draft = initialDraft(
      base({
        proposal: [
          { ...proposalRow, itemId: 'A', exception: 'SHORT_DISPUTE' },
          { ...proposalRow, itemId: 'B', exception: null },
        ],
      }),
    )
    expect(draft.rows.map((r) => r.exception)).toEqual(['SHORT_DISPUTE', null])
  })

  test('falls back to the open amount if the proposal has none', () => {
    expect(initialDraft(base({ proposal: [{ ...proposalRow, amount: null }] })).rows[0].amount).toBe(100)
  })

  test('ignores an exception outside the closed list', () => {
    expect(lineException(initialDraft(base({ proposal: [{ ...proposalRow, exception: 'NOPE' }] })))).toBeNull()
  })

  test('reads a proposal without items', () => {
    const draft = initialDraft(base({ proposal: [{ ...proposalRow, itemId: null, amount: null, category: 'BANK_FEES' }] }))
    expect(draft.rows).toEqual([])
    expect(draft.category).toBe('BANK_FEES')
  })

  test('starts empty without a proposal', () => {
    expect(initialDraft(base()).origin).toBe('empty')
  })
})

describe('draftToDecision', () => {
  const draft = {
    rows: [{ itemId: 'ARI-1', amount: 100, exception: null }],
    exception: null,
    category: null,
    origin: 'proposal' as const,
    touched: false,
  }

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
    expect(draftToDecision('T1', { ...draft, rows: [], exception: 'RETURNED' }, 5)?.exception).toBe('RETURNED')
  })

  test('gives UNIDENTIFIED its category, as the format requires', () => {
    const result = draftToDecision('T1', { ...draft, rows: [], exception: 'UNIDENTIFIED' }, 5)
    expect(result).toMatchObject({ category: 'UNIDENTIFIED', exception: 'UNIDENTIFIED', allocations: [] })
  })
})

describe('withException', () => {
  const context = { items: new Map([['ARI-1', item({ openAmount: 1000 })]]), txn: { amount: 980, currency: 'EUR', date: '2024-02-01' }, rates: [] }
  const draft = {
    rows: [{ itemId: 'ARI-1', amount: 980, exception: null }],
    exception: null,
    category: null,
    origin: 'empty' as const,
    touched: false,
  }

  test('applies the amount rule to the rows', () => {
    const next = withException(draft, 'SHORT_DISCOUNT', context)
    expect(next.rows).toEqual([{ itemId: 'ARI-1', amount: 1000, exception: 'SHORT_DISCOUNT' }])
    expect(next.touched).toBe(true)
  })

  test('removes the exception when it is picked again', () => {
    const once = withException(draft, 'SHORT_DISCOUNT', context)
    const twice = withException(once, 'SHORT_DISCOUNT', context)
    expect(twice.rows).toEqual([{ itemId: 'ARI-1', amount: 980, exception: null }])
  })

  test('UNIDENTIFIED drops the items and sets the category', () => {
    const next = withException(draft, 'UNIDENTIFIED', context)
    expect(next).toMatchObject({ rows: [], category: 'UNIDENTIFIED', exception: 'UNIDENTIFIED' })
  })

  test('sets the line exception when there are no items', () => {
    expect(withException({ ...draft, rows: [] }, 'RETURNED', context).exception).toBe('RETURNED')
  })
})

describe('withRowException', () => {
  const items = new Map([
    ['A', item({ itemId: 'A', openAmount: 100 })],
    ['B', item({ itemId: 'B', openAmount: 50 })],
  ])
  const draft = {
    rows: [
      { itemId: 'A', amount: 90, exception: null },
      { itemId: 'B', amount: 50, exception: null },
    ],
    exception: null,
    category: null,
    origin: 'empty' as const,
    touched: false,
  }
  const context = { items, txn: { amount: 140, currency: 'EUR', date: '2024-02-01' }, rates: [] }

  test('changes only that row', () => {
    const next = withRowException(draft, 'A', 'SHORT_DISPUTE', context)
    expect(next.rows).toEqual([
      { itemId: 'A', amount: 90, exception: 'SHORT_DISPUTE' },
      { itemId: 'B', amount: 50, exception: null },
    ])
  })

  test('a partial row gets what was really paid for it, not the whole movement', () => {
    const rows = [
      { itemId: 'A', amount: 100, exception: null },
      { itemId: 'B', amount: 50, exception: null },
    ]
    const next = withRowException({ ...draft, rows }, 'B', 'PARTIAL', context)
    expect(next.rows[1]).toEqual({ itemId: 'B', amount: 40, exception: 'PARTIAL' })
  })

  test('applies the amount rule to that row', () => {
    const next = withRowException(draft, 'A', 'SHORT_DISCOUNT', context)
    expect(next.rows[0]).toEqual({ itemId: 'A', amount: 100, exception: 'SHORT_DISCOUNT' })
  })

  test('removes it when picked again', () => {
    const once = withRowException(draft, 'A', 'SHORT_DISPUTE', context)
    expect(withRowException(once, 'A', 'SHORT_DISPUTE', context).rows[0].exception).toBeNull()
  })
})

describe('withCategory', () => {
  const draft = {
    rows: [{ itemId: 'ARI-1', amount: 100, exception: 'PARTIAL' as const }],
    exception: null,
    category: null,
    origin: 'empty' as const,
    touched: false,
  }

  test('drops the items, because a line cannot have both', () => {
    expect(withCategory(draft, 'BANK_FEES')).toMatchObject({ rows: [], category: 'BANK_FEES', exception: null })
  })

  test('the UNIDENTIFIED category carries its exception', () => {
    expect(withCategory(draft, 'UNIDENTIFIED').exception).toBe('UNIDENTIFIED')
  })
})

describe('proposalDecision', () => {
  test('is null without a proposal', () => {
    expect(proposalDecision(base(), 1)).toBeNull()
  })

  test('builds the decision of the proposal', () => {
    expect(proposalDecision(base({ proposal: [proposalRow] }), 1)?.allocations).toEqual([
      { itemId: 'ARI-1', amount: 100, exception: null },
    ])
  })
})
