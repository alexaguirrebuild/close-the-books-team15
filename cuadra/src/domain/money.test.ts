import { describe, expect, test } from 'vitest'
import { convert, findRate, isBalanced, parseAmount, tolerance } from './money'

describe('tolerance', () => {
  test('uses the 0.01 floor on small amounts', () => {
    expect(tolerance(1)).toBe(0.01)
  })

  test('uses 0.5 % on larger amounts, ignoring the sign', () => {
    expect(tolerance(-1000)).toBeCloseTo(5)
  })
})

describe('isBalanced', () => {
  test('accepts a difference inside the tolerance', () => {
    expect(isBalanced(1000, 1004.9)).toBe(true)
  })

  test('rejects a difference outside the tolerance', () => {
    expect(isBalanced(1000, 1005.1)).toBe(false)
  })
})

describe('findRate', () => {
  const rates = [
    { date: '2024-01-01', base: 'USD', quote: 'EUR', rate: 0.9 },
    { date: '2024-02-01', base: 'USD', quote: 'EUR', rate: 0.8 },
  ]

  test('is 1 for the same currency', () => {
    expect(findRate(rates, 'EUR', 'EUR', '2024-01-15')).toBe(1)
  })

  test('uses the latest rate on or before the date', () => {
    expect(findRate(rates, 'USD', 'EUR', '2024-01-15')).toBe(0.9)
  })

  test('inverts the pair when only the opposite exists', () => {
    expect(findRate(rates, 'EUR', 'USD', '2024-02-15')).toBeCloseTo(1 / 0.8)
  })

  test('falls back to the first rate when the date is earlier', () => {
    expect(findRate(rates, 'USD', 'EUR', '2023-01-01')).toBe(0.9)
  })

  test('is null when the pair is unknown', () => {
    expect(findRate(rates, 'USD', 'GBP', '2024-01-15')).toBeNull()
  })

  test('crosses two currencies through the euro', () => {
    const perEur = [
      { date: '2024-01-01', base: 'EUR', quote: 'USD', rate: 2 },
      { date: '2024-01-01', base: 'EUR', quote: 'GBP', rate: 0.5 },
    ]
    expect(findRate(perEur, 'USD', 'GBP', '2024-02-01')).toBeCloseTo(0.25)
  })

  test('uses the last available rate on a day without one', () => {
    const perEur = [
      { date: '2024-01-05', base: 'EUR', quote: 'USD', rate: 1.1 },
      { date: '2024-01-08', base: 'EUR', quote: 'USD', rate: 1.2 },
    ]
    expect(findRate(perEur, 'EUR', 'USD', '2024-01-07')).toBe(1.1)
  })
})

describe('convert', () => {
  test('converts with the rate at the date', () => {
    const rates = [{ date: '2024-01-01', base: 'USD', quote: 'EUR', rate: 0.5 }]
    expect(convert(100, 'USD', 'EUR', '2024-06-01', rates)).toBe(50)
  })
})

describe('parseAmount', () => {
  test('reads the way amounts are shown on screen', () => {
    expect(parseAmount('1.234,56')).toBe(1234.56)
    expect(parseAmount('89.850,73')).toBe(89850.73)
  })

  test('reads a plain decimal comma or dot', () => {
    expect(parseAmount('12,5')).toBe(12.5)
    expect(parseAmount('12.5')).toBe(12.5)
    expect(parseAmount('1234.56')).toBe(1234.56)
  })

  test('reads thousands without decimals', () => {
    expect(parseAmount('1.234')).toBe(1234)
    expect(parseAmount('1.234.567')).toBe(1234567)
  })

  test('reads the other locale when both separators appear', () => {
    expect(parseAmount('1,234.56')).toBe(1234.56)
  })

  test('is null for what is not a number', () => {
    expect(parseAmount('')).toBeNull()
    expect(parseAmount('abc')).toBeNull()
  })
})
