import { describe, expect, test } from 'vitest'
import { minConfidenceFromPercent } from './exportSubmission'

describe('minConfidenceFromPercent', () => {
  test('turns a percentage into a confidence between 0 and 1', () => {
    expect(minConfidenceFromPercent('80')).toBe(0.8)
    expect(minConfidenceFromPercent('0')).toBe(0)
    expect(minConfidenceFromPercent('100')).toBe(1)
  })

  test('limits a percentage out of range', () => {
    expect(minConfidenceFromPercent('150')).toBe(1)
    expect(minConfidenceFromPercent('-5')).toBe(0)
  })

  test('gives nothing for a field without a usable number, because an empty one is not a 0', () => {
    for (const raw of ['', '  ', 'abc', 'Infinity', 'NaN']) expect(minConfidenceFromPercent(raw)).toBeNull()
  })
})
