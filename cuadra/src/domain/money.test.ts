import { describe, expect, test } from 'vitest'
import { convert, findRate, formatExportAmount, isBalanced, tolerance } from './money'

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

describe('formatExportAmount', () => {
  test('always writes two decimals with a dot', () => {
    expect(formatExportAmount(1234.5)).toBe('1234.50')
  })

  test('keeps the sign of the item', () => {
    expect(formatExportAmount(-20)).toBe('-20.00')
  })

  test('normalises negative zero', () => {
    expect(formatExportAmount(-0.001)).toBe('0.00')
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
})

describe('convert', () => {
  test('converts with the rate at the date', () => {
    const rates = [{ date: '2024-01-01', base: 'USD', quote: 'EUR', rate: 0.5 }]
    expect(convert(100, 'USD', 'EUR', '2024-06-01', rates)).toBe(50)
  })
})
