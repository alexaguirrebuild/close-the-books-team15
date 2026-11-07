import { describe, expect, test } from 'vitest'
import { computeScore, type ScoreRow } from './score'

const row = (
  txn: string,
  item: string | null,
  amount: number | null,
  exception: string | null = null,
  category: string | null = null,
  segment: string | null = null,
): ScoreRow => ({ txn, item, amount, exception, category, segment })

describe('computeScore', () => {
  test('gives the same figures as the official scorer on a reference case', () => {
    const gold = [
      row('T1', 'ARI-1', 100, null, null, 'ar_1to1'),
      row('T2', 'ARI-2', 60, null, null, 'ar_1toN'),
      row('T2', 'ARI-3', 40, 'PARTIAL', null, 'ar_1toN'),
      row('T3', 'API-4', 1000, 'SHORT_DISCOUNT', null, 'ap_short'),
      row('T4', null, null, null, 'BANK_FEES', 'nonitem'),
      row('T5', null, null, 'UNIDENTIFIED', 'UNIDENTIFIED', 'unidentified'),
      row('T6', 'ARI-5', 50, 'RETURNED', null, 'ar_returned'),
    ]
    const pred = [
      row('T1', 'ARI-1', 100),
      row('T2', 'ARI-2', 60),
      row('T2', 'ARI-3', 40),
      row('T3', 'API-4', 980, 'SHORT_DISCOUNT'),
      row('T4', null, null, null, 'BANK_FEES'),
      row('T5', null, null, 'UNIDENTIFIED', 'UNIDENTIFIED'),
      row('T6', 'ARI-9', 50, 'RETURNED'),
    ]
    const result = computeScore(pred, gold, ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7'])
    expect(result.total).toBe(52.86)
    expect(result.allocationF1).toBeCloseTo(0.6, 6)
    expect(result.allocationPrecision).toBeCloseTo(0.6, 6)
    expect(result.allocationRecall).toBeCloseTo(0.6, 6)
    expect(result.transactionAccuracy).toBeCloseTo(0.428571, 6)
    expect(result.exceptionMacroF1).toBeCloseTo(0.5, 6)
    expect(result.segments.ar_1to1).toEqual({ n: 1, accuracy: 1 })
    expect(result.segments.ar_1toN).toEqual({ n: 1, accuracy: 0 })
    expect(result.segments.unlabelled).toEqual({ n: 1, accuracy: 0 })
  })

  test('is 100 for a perfect submission', () => {
    const gold = [row('T1', 'A', 100), row('T2', null, null, 'UNIDENTIFIED', 'UNIDENTIFIED')]
    const result = computeScore(gold, gold)
    expect(result.allocationF1).toBe(1)
    expect(result.transactionAccuracy).toBe(1)
    expect(result.exceptionMacroF1).toBe(1)
    expect(result.total).toBe(100)
  })

  test('accepts an amount inside max(0.01, 0.5 %)', () => {
    expect(computeScore([row('T1', 'A', 1004)], [row('T1', 'A', 1000)]).allocationF1).toBe(1)
  })

  test('rejects an amount outside the tolerance', () => {
    expect(computeScore([row('T1', 'A', 1006)], [row('T1', 'A', 1000)]).allocationF1).toBe(0)
  })

  test('counts a missing allocation against recall', () => {
    const gold = [row('T1', 'A', 60), row('T1', 'B', 40)]
    const result = computeScore([row('T1', 'A', 60)], gold)
    expect(result.allocationRecall).toBeCloseTo(0.5)
    expect(result.allocationPrecision).toBe(1)
  })

  test('fails the line if any row differs', () => {
    const gold = [row('T1', 'A', 60), row('T1', 'B', 40)]
    expect(computeScore([row('T1', 'A', 60)], gold).transactionAccuracy).toBe(0)
  })

  test('compares the exception of each row, not one per line', () => {
    const gold = [row('T1', 'A', 60, 'SHORT_DISPUTE'), row('T1', 'B', 40)]
    const sameOnBoth = [row('T1', 'A', 60, 'SHORT_DISPUTE'), row('T1', 'B', 40, 'SHORT_DISPUTE')]
    expect(computeScore(sameOnBoth, gold).transactionAccuracy).toBe(0)
    expect(computeScore(gold, gold).transactionAccuracy).toBe(1)
  })

  test('counts a line missing from the submission as failed', () => {
    const gold = [row('T1', 'A', 60), row('T2', 'B', 40)]
    expect(computeScore([row('T1', 'A', 60)], gold).transactionAccuracy).toBe(0.5)
  })

  test('counts a bank line the golden does not mention', () => {
    expect(computeScore([row('T1', 'A', 60)], [row('T1', 'A', 60)], ['T1', 'T2']).transactionAccuracy).toBe(0.5)
  })

  test('requires the category to match on rows without an item', () => {
    const result = computeScore([row('T1', null, null, null, 'FEES')], [row('T1', null, null, null, 'RENT')])
    expect(result.transactionAccuracy).toBe(0)
  })

  test('averages only the exception classes present in the golden', () => {
    const gold = [row('T1', null, null, 'RETURNED'), row('T2', null, null, 'UNIDENTIFIED')]
    const pred = [row('T1', null, null, 'RETURNED'), row('T2', null, null, 'RETURNED')]
    expect(computeScore(pred, gold).exceptionMacroF1).toBeCloseTo((2 / 3 + 0) / 2)
    const noise = [row('T1', 'A', 1, 'PARTIAL')]
    expect(computeScore(noise, [row('T1', 'A', 1, 'RETURNED')]).exceptionMacroF1).toBe(0)
  })

  test('is 1 on exceptions when the golden has none', () => {
    expect(computeScore([row('T1', 'A', 1)], [row('T1', 'A', 1)]).exceptionMacroF1).toBe(1)
  })
})
