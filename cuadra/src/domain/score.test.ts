import { describe, expect, test } from 'vitest'
import { computeScore, type ScoreRow } from './score'

const row = (
  txn: string,
  item: string | null,
  amount: number | null,
  exception: string | null = null,
  category: string | null = null,
): ScoreRow => ({ txn, item, amount, exception, category })

describe('computeScore', () => {
  test('is 1 for a perfect submission', () => {
    const gold = [row('T1', 'A', 100), row('T2', null, null, 'UNIDENTIFIED')]
    const result = computeScore(gold, gold)
    expect(result.allocationF1).toBe(1)
    expect(result.transactionAccuracy).toBe(1)
    expect(result.exceptionMacroF1).toBe(1)
    expect(result.total).toBeCloseTo(1)
  })

  test('applies the published weights', () => {
    const gold = [row('T1', 'A', 100), row('T2', 'B', 50, 'PARTIAL')]
    const pred = [row('T1', 'A', 100), row('T2', 'C', 50)]
    const result = computeScore(pred, gold)
    const expected =
      0.5 * result.allocationF1 + 0.3 * result.transactionAccuracy + 0.2 * result.exceptionMacroF1
    expect(result.total).toBeCloseTo(expected)
  })

  test('accepts an amount inside max(0.01, 0.5 %)', () => {
    const result = computeScore([row('T1', 'A', 1004)], [row('T1', 'A', 1000)])
    expect(result.allocationF1).toBe(1)
  })

  test('rejects an amount outside the tolerance', () => {
    const result = computeScore([row('T1', 'A', 1006)], [row('T1', 'A', 1000)])
    expect(result.allocationF1).toBe(0)
  })

  test('counts a missing allocation against recall', () => {
    const gold = [row('T1', 'A', 60), row('T1', 'B', 40)]
    const result = computeScore([row('T1', 'A', 60)], gold)
    expect(result.allocationRecall).toBeCloseTo(0.5)
    expect(result.allocationPrecision).toBe(1)
  })

  test('marks the transaction as failed if any row differs', () => {
    const gold = [row('T1', 'A', 60), row('T1', 'B', 40)]
    expect(computeScore([row('T1', 'A', 60)], gold).transactionAccuracy).toBe(0)
  })

  test('counts a movement missing from the submission as failed', () => {
    const gold = [row('T1', 'A', 60), row('T2', 'B', 40)]
    expect(computeScore([row('T1', 'A', 60)], gold).transactionAccuracy).toBe(0.5)
  })

  test('requires the category to match on rows without an item', () => {
    const result = computeScore(
      [row('T1', null, null, null, 'FEES')],
      [row('T1', null, null, null, 'RENT')],
    )
    expect(result.transactionAccuracy).toBe(0)
  })

  test('averages the F1 of the exception classes that appear', () => {
    const gold = [row('T1', null, null, 'RETURNED'), row('T2', null, null, 'UNIDENTIFIED')]
    const pred = [row('T1', null, null, 'RETURNED'), row('T2', null, null, 'RETURNED')]
    const result = computeScore(pred, gold)
    expect(result.exceptionMacroF1).toBeCloseTo((2 / 3 + 0) / 2)
  })

  test('is 1 on exceptions when neither side has any', () => {
    expect(computeScore([row('T1', 'A', 1)], [row('T1', 'A', 1)]).exceptionMacroF1).toBe(1)
  })
})
