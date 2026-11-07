import { describe, expect, test } from 'vitest'
import { autoAllocate, computeResidual, itemFromKind, suggestExceptions } from './allocation'
import type { Item } from './types'

const makeItem = (over: Partial<Item>): Item => ({
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

const txn = { amount: 100, currency: 'EUR', date: '2024-02-01' }

describe('itemFromKind', () => {
  test('recognises the official prefixes', () => {
    expect(itemFromKind('ARI-1')).toBe('AR')
    expect(itemFromKind('API-1')).toBe('AP')
    expect(itemFromKind('ARC-1')).toBe('CN')
    expect(itemFromKind('X-1')).toBe('OTHER')
  })
})

describe('autoAllocate', () => {
  test('gives each item its open amount until the movement runs out', () => {
    const items = [
      makeItem({ itemId: 'A', openAmount: 60 }),
      makeItem({ itemId: 'B', openAmount: 80 }),
    ]
    const rows = autoAllocate(items, txn, [])
    expect(rows).toEqual([
      { itemId: 'A', amount: 60 },
      { itemId: 'B', amount: 40 },
    ])
  })

  test('adds a credit note to what can be paid', () => {
    const items = [
      makeItem({ itemId: 'INV', openAmount: 120 }),
      makeItem({ itemId: 'ARC-1', kind: 'CN', amount: -20, openAmount: -20 }),
    ]
    const rows = autoAllocate(items, txn, [])
    expect(rows).toEqual([
      { itemId: 'INV', amount: 120 },
      { itemId: 'ARC-1', amount: -20 },
    ])
  })

  test('converts to the currency of the item', () => {
    const rates = [{ date: '2024-01-01', base: 'EUR', quote: 'USD', rate: 2 }]
    const rows = autoAllocate([makeItem({ currency: 'USD', openAmount: 500 })], txn, rates)
    expect(rows).toEqual([{ itemId: 'ARI-1', amount: 200 }])
  })

  test('keeps the negative sign of an item stored as negative', () => {
    const rows = autoAllocate([makeItem({ amount: -100, openAmount: -100 })], txn, [])
    expect(rows).toEqual([{ itemId: 'ARI-1', amount: -100 }])
  })
})

describe('computeResidual', () => {
  const inv = makeItem({ itemId: 'INV', openAmount: 120 })
  const cn = makeItem({ itemId: 'CN', kind: 'CN', amount: -20, openAmount: -20 })

  test('is zero when the allocation adds up', () => {
    const result = computeResidual(
      txn,
      [
        { itemId: 'INV', amount: 120 },
        { itemId: 'CN', amount: -20 },
      ],
      new Map([
        ['INV', inv],
        ['CN', cn],
      ]),
      [],
    )
    expect(result.residual).toBeCloseTo(0)
    expect(result.balanced).toBe(true)
  })

  test('is positive when the movement is not fully applied', () => {
    const result = computeResidual(
      txn,
      [{ itemId: 'INV', amount: 70 }],
      new Map([['INV', inv]]),
      [],
    )
    expect(result.residual).toBeCloseTo(30)
    expect(result.balanced).toBe(false)
  })

  test('nets a credit note stored as a positive amount', () => {
    const positiveCn = makeItem({ itemId: 'CN', kind: 'CN', amount: 20, openAmount: 20 })
    const result = computeResidual(
      txn,
      [
        { itemId: 'INV', amount: 120 },
        { itemId: 'CN', amount: 20 },
      ],
      new Map([
        ['INV', inv],
        ['CN', positiveCn],
      ]),
      [],
    )
    expect(result.residual).toBeCloseTo(0)
  })

  test('adds a lone credit note paid out as a refund', () => {
    const refund = { amount: -20, currency: 'EUR', date: '2024-02-01' }
    const result = computeResidual(refund, [{ itemId: 'CN', amount: -20 }], new Map([['CN', cn]]), [])
    expect(result.balanced).toBe(true)
  })

  test('reports what stays open in the items when the movement is fully applied', () => {
    const result = computeResidual(txn, [{ itemId: 'INV', amount: 100 }], new Map([['INV', inv]]), [])
    expect(result.balanced).toBe(true)
    expect(result.openGap).toBeCloseTo(20)
  })

  test('has no open gap when the items are settled', () => {
    const full = makeItem({ itemId: 'FULL', openAmount: 100 })
    const result = computeResidual(txn, [{ itemId: 'FULL', amount: 100 }], new Map([['FULL', full]]), [])
    expect(result.openGap).toBeCloseTo(0)
  })

  test('reports a currency without a rate', () => {
    const usd = makeItem({ itemId: 'U', currency: 'USD' })
    const result = computeResidual(txn, [{ itemId: 'U', amount: 100 }], new Map([['U', usd]]), [])
    expect(result.missingRate).toBe(true)
  })
})

describe('suggestExceptions', () => {
  test('suggests nothing when it adds up', () => {
    expect(suggestExceptions({ residual: 0, balanced: true, fxInvolved: false, txnAmount: 100, discountPct: null })).toEqual([])
  })

  test('suggests short payments when it adds up but the item stays open', () => {
    const suggestions = suggestExceptions({ residual: 0, balanced: true, fxInvolved: false, txnAmount: 70, discountPct: null, openGap: 30 })
    expect(suggestions[0]).toBe('PARTIAL')
  })

  test('recognises the discount from the open gap', () => {
    const suggestions = suggestExceptions({ residual: 0, balanced: true, fxInvolved: false, txnAmount: 98, discountPct: 2, openGap: 2 })
    expect(suggestions[0]).toBe('SHORT_DISCOUNT')
  })

  test('suggests overpayment when the movement exceeds the items', () => {
    expect(suggestExceptions({ residual: 30, balanced: false, fxInvolved: false, txnAmount: 130, discountPct: null })[0]).toBe('OVERPAYMENT')
  })

  test('suggests short payments when the items exceed the movement', () => {
    const suggestions = suggestExceptions({ residual: -30, balanced: false, fxInvolved: false, txnAmount: 70, discountPct: null })
    expect(suggestions).toContain('PARTIAL')
    expect(suggestions).toContain('SHORT_DISPUTE')
  })

  test('puts the discount first when the shortfall matches the payment terms', () => {
    const suggestions = suggestExceptions({ residual: -2, balanced: false, fxInvolved: false, txnAmount: 98, discountPct: 2 })
    expect(suggestions[0]).toBe('SHORT_DISCOUNT')
  })

  test('puts FX first when a conversion is involved', () => {
    const suggestions = suggestExceptions({ residual: 5, balanced: false, fxInvolved: true, txnAmount: 105, discountPct: null })
    expect(suggestions[0]).toBe('FX_DIFFERENCE')
  })
})
