import { describe, expect, test } from 'vitest'
import { diffAgainstGolden, type GoldenRow } from './goldenDiff'

const gold = (item: string | null, amount: number | null, exception: string | null = null, category: string | null = null): GoldenRow => ({
  item,
  amount,
  exception,
  category,
  segment: 'ar_1to1',
  difficulty: 1,
})
const row = (itemId: string, amount: number, exception: 'PARTIAL' | 'SHORT_DISPUTE' | null = null) => ({ itemId, amount, exception })
const line = (over = {}) => ({ rows: [], category: null, exception: null, ...over })

describe('diffAgainstGolden', () => {
  test('accepts the same items and amounts', () => {
    const diff = diffAgainstGolden(line({ rows: [row('A', 100)] }), [gold('A', 100)])
    expect(diff).toMatchObject({ ok: true, kind: 'ok' })
    expect(diff.text).toBe('Coincide con el golden')
  })

  test('accepts an amount within the tolerance of the scorer', () => {
    expect(diffAgainstGolden(line({ rows: [row('A', 100.4)] }), [gold('A', 100)]).ok).toBe(true)
  })

  test('reports a wrong item and which one was expected', () => {
    const diff = diffAgainstGolden(line({ rows: [row('B', 100)] }), [gold('A', 100)])
    expect(diff.kind).toBe('wrong_item')
    expect(diff.rows).toEqual([
      expect.objectContaining({ item: 'A', state: 'missing', goldAmount: 100 }),
      expect.objectContaining({ item: 'B', state: 'extra', myAmount: 100 }),
    ])
  })

  test('reports a wrong amount', () => {
    const diff = diffAgainstGolden(line({ rows: [row('A', 90)] }), [gold('A', 100)])
    expect(diff.kind).toBe('wrong_amount')
    expect(diff.rows[0]).toMatchObject({ state: 'amount', myAmount: 90, goldAmount: 100 })
  })

  test('reports a wrong exception on the row', () => {
    const diff = diffAgainstGolden(line({ rows: [row('A', 100, 'PARTIAL')] }), [gold('A', 100, 'SHORT_DISPUTE')])
    expect(diff.kind).toBe('wrong_exception')
    expect(diff.rows[0]).toMatchObject({ state: 'exception', myException: 'PARTIAL', goldException: 'SHORT_DISPUTE' })
  })

  test('reports a wrong category on a line without items', () => {
    const diff = diffAgainstGolden(line({ category: 'PAYROLL' }), [gold(null, null, null, 'BANK_FEES')])
    expect(diff.kind).toBe('wrong_category')
    expect(diff.text).toContain('BANK_FEES')
  })

  test('accepts the right category and exception', () => {
    const diff = diffAgainstGolden(line({ category: 'UNIDENTIFIED', exception: 'UNIDENTIFIED' }), [gold(null, null, 'UNIDENTIFIED', 'UNIDENTIFIED')])
    expect(diff.ok).toBe(true)
  })

  test('reports items where the golden has a category', () => {
    expect(diffAgainstGolden(line({ rows: [row('A', 100)] }), [gold(null, null, null, 'BANK_FEES')]).kind).toBe('wrong_item')
  })

  test('says when nothing was decided', () => {
    expect(diffAgainstGolden(line(), [gold('A', 100)]).kind).toBe('no_row')
  })
})
