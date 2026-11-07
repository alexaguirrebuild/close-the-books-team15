import { describe, expect, test } from 'vitest'
import { minConfidenceFromPercent, percentFromMinConfidence } from './exportSubmission'

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

describe('percentFromMinConfidence', () => {
  test('keeps the decimals that were typed', () => {
    expect(percentFromMinConfidence(0.955)).toBe('95.5')
    expect(percentFromMinConfidence(0.123456)).toBe('12.3456')
  })

  test('shows a whole number without decimals', () => {
    expect(percentFromMinConfidence(0.95)).toBe('95')
    expect(percentFromMinConfidence(1)).toBe('100')
    expect(percentFromMinConfidence(0)).toBe('0')
  })

  test('hides the noise of floating point', () => {
    expect(percentFromMinConfidence(0.07)).toBe('7')
    expect(percentFromMinConfidence(0.29)).toBe('29')
  })

  test('goes back to what minConfidenceFromPercent read', () => {
    expect(minConfidenceFromPercent(percentFromMinConfidence(0.955))).toBe(0.955)
  })
})
