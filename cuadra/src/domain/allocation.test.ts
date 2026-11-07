import { describe, expect, test } from 'vitest'
import { autoAllocate, computeResidual, reallocate } from './allocation'
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
  issueDate: '2023-12-01',
  po: '',
  isOpen: true,
  terms: '',
  ...over,
})

const txn = { amount: 100, currency: 'EUR', date: '2024-02-01' }

describe('autoAllocate', () => {
  test('gives each item its open amount until the movement runs out', () => {
    const items = [
      makeItem({ itemId: 'A', openAmount: 60 }),
      makeItem({ itemId: 'B', openAmount: 80 }),
    ]
    const rows = autoAllocate(items, txn, [])
    expect(rows).toEqual([
      { itemId: 'A', amount: 60, exception: null },
      { itemId: 'B', amount: 40, exception: null },
    ])
  })

  test('adds a credit note to what can be paid', () => {
    const items = [
      makeItem({ itemId: 'INV', openAmount: 120 }),
      makeItem({ itemId: 'ARC-1', kind: 'CN', amount: 20, openAmount: 20 }),
    ]
    const rows = autoAllocate(items, txn, [])
    expect(rows).toEqual([
      { itemId: 'INV', amount: 120, exception: null },
      { itemId: 'ARC-1', amount: 20, exception: null },
    ])
  })

  test('converts to the currency of the item', () => {
    const rates = [{ date: '2024-01-01', base: 'EUR', quote: 'USD', rate: 2 }]
    const rows = autoAllocate([makeItem({ currency: 'USD', openAmount: 500 })], txn, rates)
    expect(rows).toEqual([{ itemId: 'ARI-1', amount: 200, exception: null }])
  })

  test('always gives a positive amount, as the submission format requires', () => {
    const rows = autoAllocate([makeItem({ amount: -100, openAmount: -100 })], txn, [])
    expect(rows).toEqual([{ itemId: 'ARI-1', amount: 100, exception: null }])
  })
})

describe('reallocate', () => {
  const a = makeItem({ itemId: 'A', openAmount: 60 })
  const b = makeItem({ itemId: 'B', openAmount: 80 })
  const c = makeItem({ itemId: 'C', openAmount: 30 })
  const items = new Map([a, b, c].map((item) => [item.itemId, item]))
  const row = (itemId: string, amount: number, exception: 'SHORT_DISPUTE' | null = null) => ({ itemId, amount, exception })
  const change = (current: ReturnType<typeof row>[], nextIds: string[]) => reallocate({ current, nextIds, items, txn, rates: [] })

  test('spreads the cash when nothing was edited', () => {
    expect(change([], ['A', 'B'])).toEqual([row('A', 60), row('B', 40)])
  })

  test('gives the cash back to the rest when an item is removed', () => {
    expect(change([row('A', 60), row('B', 40)], ['B'])).toEqual([row('B', 80)])
  })

  test('keeps an amount the reviewer typed when adding another item', () => {
    expect(change([row('A', 25)], ['A', 'B'])).toEqual([row('A', 25), row('B', 75)])
  })

  test('keeps the exception of a row when adding another, and does not copy it', () => {
    expect(change([row('A', 54, 'SHORT_DISPUTE')], ['A', 'C'])).toEqual([row('A', 54, 'SHORT_DISPUTE'), row('C', 30)])
  })

  test('never drops a row of an earlier phase, which is not loaded', () => {
    expect(change([row('OLD', 10)], ['OLD', 'C'])).toEqual([row('OLD', 10), row('C', 30)])
  })
})

describe('computeResidual', () => {
  const inv = makeItem({ itemId: 'INV', openAmount: 120 })
  const cn = makeItem({ itemId: 'CN', kind: 'CN', amount: -20, openAmount: -20 })

  test('is zero when the allocation adds up', () => {
    const result = computeResidual(
      txn,
      [
        { itemId: 'INV', amount: 120, exception: null },
        { itemId: 'CN', amount: 20, exception: null },
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
      [{ itemId: 'INV', amount: 70, exception: null }],
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
        { itemId: 'INV', amount: 120, exception: null },
        { itemId: 'CN', amount: 20, exception: null },
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
    const result = computeResidual(refund, [{ itemId: 'CN', amount: 20, exception: null }], new Map([['CN', cn]]), [])
    expect(result.balanced).toBe(true)
  })

  test('reports what stays open in the items when the movement is fully applied', () => {
    const result = computeResidual(txn, [{ itemId: 'INV', amount: 100, exception: null }], new Map([['INV', inv]]), [])
    expect(result.balanced).toBe(true)
    expect(result.openGap).toBeCloseTo(20)
  })

  test('has no open gap when the items are settled', () => {
    const full = makeItem({ itemId: 'FULL', openAmount: 100 })
    const result = computeResidual(txn, [{ itemId: 'FULL', amount: 100, exception: null }], new Map([['FULL', full]]), [])
    expect(result.openGap).toBeCloseTo(0)
  })

  test('counts an item of an earlier phase at its amount, so a return can still add up', () => {
    const result = computeResidual({ ...txn, amount: -100 }, [{ itemId: 'OLD', amount: 100, exception: 'RETURNED' }], new Map(), [])
    expect(result.balanced).toBe(true)
  })

  test('reports a currency without a rate', () => {
    const usd = makeItem({ itemId: 'U', currency: 'USD' })
    const result = computeResidual(txn, [{ itemId: 'U', amount: 100, exception: null }], new Map([['U', usd]]), [])
    expect(result.missingRate).toBe(true)
  })
})

