import { describe, expect, test } from 'vitest'
import { applyException, clearsInFull, suggestExceptions, type SuggestInput } from './policy'
import type { Allocation, Item } from './types'

const makeItem = (over: Partial<Item>): Item => ({
  itemId: 'INV',
  kind: 'AR',
  printed: 'F1',
  partyId: 'C1',
  partyName: 'Acme',
  amount: 1000,
  openAmount: 1000,
  currency: 'EUR',
  dueDate: '2024-03-01',
  issueDate: '2024-01-01',
  isOpen: true,
  terms: '',
  po: '',
  ...over,
})

const row = (itemId: string, amount: number, exception: Allocation['exception'] = null): Allocation => ({ itemId, amount, exception })
const items = (...list: Item[]) => new Map(list.map((item) => [item.itemId, item]))
const txn = { amount: 980, currency: 'EUR', date: '2024-01-05' }
const invoice = makeItem({})

describe('clearsInFull', () => {
  test('follows POLICIES section 2', () => {
    for (const code of ['SHORT_DISCOUNT', 'SHORT_BANK_CHARGES', 'SHORT_WITHHOLDING', 'FX_DIFFERENCE', 'OVERPAYMENT'] as const) {
      expect(clearsInFull(code)).toBe(true)
    }
    for (const code of ['PARTIAL', 'SHORT_DISPUTE', 'DUPLICATE_PAYMENT', 'RETURNED'] as const) {
      expect(clearsInFull(code)).toBe(false)
    }
  })
})

describe('applyException', () => {
  const apply = (rows: Allocation[], code: Allocation['exception'], map = items(invoice), movement = txn, rates: never[] = []) =>
    applyException({ rows, code, items: map, txn: movement, rates })

  test('clears the whole open amount with an accepted short payment', () => {
    expect(apply([row('INV', 980)], 'SHORT_DISCOUNT')).toEqual([row('INV', 1000, 'SHORT_DISCOUNT')])
  })

  test('keeps what was paid when the item stays open', () => {
    expect(apply([row('INV', 1000)], 'PARTIAL')).toEqual([row('INV', 980, 'PARTIAL')])
    expect(apply([row('INV', 1000)], 'SHORT_DISPUTE')).toEqual([row('INV', 980, 'SHORT_DISPUTE')])
  })

  test('respects an amount the reviewer typed below the cash', () => {
    expect(apply([row('INV', 300)], 'PARTIAL')).toEqual([row('INV', 300, 'PARTIAL')])
  })

  test('clears only the open amount on an overpayment', () => {
    const over = { ...txn, amount: 1100 }
    expect(apply([row('INV', 1000)], 'OVERPAYMENT', items(invoice), over)).toEqual([row('INV', 1000, 'OVERPAYMENT')])
  })

  test('leaves credit notes without exception and at their full amount', () => {
    const note = makeItem({ itemId: 'CN', kind: 'CN', openAmount: 100, amount: 100 })
    const rows = apply([row('INV', 1000), row('CN', 100)], 'FX_DIFFERENCE', items(invoice, note), { ...txn, amount: 900 })
    expect(rows).toEqual([row('INV', 1000, 'FX_DIFFERENCE'), row('CN', 100)])
  })

  test('a returned operation reaches its credit notes too, as the golden does', () => {
    const note = makeItem({ itemId: 'CN', kind: 'CN', openAmount: 100, amount: 100 })
    const rows = apply([row('INV', 1000), row('CN', 100)], 'RETURNED', items(invoice, note), { ...txn, amount: -900 })
    expect(rows).toEqual([row('INV', 1000, 'RETURNED'), row('CN', 100, 'RETURNED')])
  })

  test('marks every invoice of the line', () => {
    const other = makeItem({ itemId: 'B', openAmount: 500 })
    const rows = apply([row('INV', 1000), row('B', 500)], 'FX_DIFFERENCE', items(invoice, other), { ...txn, amount: 1480 })
    expect(rows.map((r) => r.exception)).toEqual(['FX_DIFFERENCE', 'FX_DIFFERENCE'])
    expect(rows.map((r) => r.amount)).toEqual([1000, 500])
  })

  test('clears the amount in the currency of the item', () => {
    const usd = makeItem({ itemId: 'USD', currency: 'USD', openAmount: 1100, amount: 1100 })
    const rates = [{ date: '2024-01-01', base: 'EUR', quote: 'USD', rate: 1.1 }]
    const rows = applyException({ rows: [row('USD', 1000)], code: 'FX_DIFFERENCE', items: items(usd), txn, rates })
    expect(rows).toEqual([row('USD', 1100, 'FX_DIFFERENCE')])
  })

  test('goes back to the cash when the exception is removed', () => {
    expect(apply([row('INV', 1000, 'SHORT_DISCOUNT')], null)).toEqual([row('INV', 980)])
  })

  test('works out the cash from every row when only one carries the exception', () => {
    const a = makeItem({ itemId: 'A', openAmount: 600, amount: 600 })
    const b = makeItem({ itemId: 'B', openAmount: 500, amount: 500 })
    const movement = { ...txn, amount: 1000 }
    const rows = applyException({ rows: [row('A', 600), row('B', 500)], code: 'SHORT_DISPUTE', onlyItemId: 'B', items: items(a, b), txn: movement, rates: [] })
    expect(rows).toEqual([row('A', 600), row('B', 400, 'SHORT_DISPUTE')])
  })

  test('keeps a row whose item is unknown', () => {
    expect(apply([row('GONE', 5)], 'PARTIAL')).toEqual([row('GONE', 5, 'PARTIAL')])
  })
})

describe('suggestExceptions', () => {
  const terms = new Map([['D2N30', { pct: 2, days: 10 }]])
  const input = (over: Partial<SuggestInput>): SuggestInput => ({
    txn: { amount: 980, currency: 'EUR', date: '2024-01-05' },
    rows: [invoice],
    terms,
    rates: [],
    alreadySettled: false,
    ...over,
  })
  const codes = (over: Partial<SuggestInput>) => suggestExceptions(input(over)).map((s) => s.code)

  test('suggests nothing when it adds up to the cent', () => {
    expect(codes({ txn: { ...txn, amount: 1000 } })).toEqual([])
    expect(codes({ txn: { ...txn, amount: 999.99 } })).toEqual([])
  })

  test('a small gap inside the tolerance still needs its exception, as in every such golden line', () => {
    const large = [makeItem({ openAmount: 10000, amount: 10000 })]
    expect(codes({ rows: large, txn: { ...txn, amount: 9975 } })[0]).toBe('SHORT_BANK_CHARGES')
    expect(codes({ rows: large, txn: { ...txn, amount: 10025 } })[0]).toBe('OVERPAYMENT')
  })

  test('suggests nothing without items', () => {
    expect(codes({ rows: [] })).toEqual([])
  })

  test('recognises an early payment discount inside its window', () => {
    const [first] = suggestExceptions(input({ rows: [makeItem({ terms: 'D2N30' })] }))
    expect(first.code).toBe('SHORT_DISCOUNT')
    expect(first.why).toContain('2 %')
  })

  test('accepts the three days of grace after the discount window', () => {
    const rows = [makeItem({ terms: 'D2N30', issueDate: '2024-01-01' })]
    expect(codes({ rows, txn: { ...txn, date: '2024-01-14' } })[0]).toBe('SHORT_DISCOUNT')
  })

  test('treats a discount taken too late as a partial payment', () => {
    const rows = [makeItem({ terms: 'D2N30', issueDate: '2024-01-01' })]
    const [first] = suggestExceptions(input({ rows, txn: { ...txn, date: '2024-01-15' } }))
    expect(first.code).toBe('PARTIAL')
    expect(first.why).toContain('fuera de plazo')
  })

  test('recognises the withholding rates', () => {
    expect(codes({ txn: { ...txn, amount: 900 } })[0]).toBe('SHORT_WITHHOLDING')
    expect(codes({ txn: { ...txn, amount: 987.5 } })[0]).toBe('SHORT_WITHHOLDING')
  })

  test('recognises bank charges within 60 EUR and 2 %', () => {
    expect(codes({ txn: { ...txn, amount: 982 } })[0]).toBe('SHORT_BANK_CHARGES')
  })

  test('does not call a larger gap bank charges', () => {
    const large = [makeItem({ openAmount: 10000, amount: 10000 })]
    const suggested = codes({ rows: large, txn: { ...txn, amount: 9900 } })
    expect(suggested).not.toContain('SHORT_BANK_CHARGES')
    expect(suggested[0]).toBe('PARTIAL')
  })

  test('always offers partial and dispute on a short payment', () => {
    const suggested = codes({ txn: { ...txn, amount: 600 } })
    expect(suggested).toContain('PARTIAL')
    expect(suggested).toContain('SHORT_DISPUTE')
  })

  test('suggests overpayment when more cash arrived than the invoice', () => {
    expect(codes({ txn: { ...txn, amount: 1100 } })[0]).toBe('OVERPAYMENT')
  })

  test('suggests a duplicate when the item was already settled', () => {
    expect(codes({ txn: { ...txn, amount: 1000 }, alreadySettled: true })[0]).toBe('DUPLICATE_PAYMENT')
  })

  test('puts FX first when the invoice is in another currency', () => {
    const usd = makeItem({ currency: 'USD', openAmount: 1100, amount: 1100 })
    const rates = [{ date: '2024-01-01', base: 'EUR', quote: 'USD', rate: 1.1 }]
    expect(codes({ rows: [usd], rates, txn: { ...txn, amount: 995 } })[0]).toBe('FX_DIFFERENCE')
  })

  test('asks for an exception when the typed amounts leave the movement unapplied', () => {
    expect(codes({ txn: { ...txn, amount: 1000 }, unbalanced: true })).toEqual(['PARTIAL', 'SHORT_DISPUTE'])
  })

  test('nets credit notes before comparing', () => {
    const note = makeItem({ itemId: 'CN', kind: 'CN', openAmount: 20, amount: 20 })
    expect(codes({ rows: [invoice, note] })).toEqual([])
  })
})
