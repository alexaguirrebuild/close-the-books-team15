import { describe, expect, test } from 'vitest'
import { extractPrintedTokens, highlightSegments, normalizePrinted } from './printed'

describe('normalizePrinted', () => {
  test('uppercases and removes separators', () => {
    expect(normalizePrinted('f-2024/00123')).toBe('F202400123')
  })
})

describe('extractPrintedTokens', () => {
  test('finds a printed number with its position', () => {
    const [token] = extractPrintedTokens('PAGO FRA F-2024/00123 ACME')
    expect(token).toMatchObject({ text: 'F-2024/00123', start: 9, norm: 'F202400123' })
  })

  test('finds several numbers in one concept', () => {
    const norms = extractPrintedTokens('FRAS 100234, 100235').map((t) => t.norm)
    expect(norms).toEqual(['100234', '100235'])
  })

  test('ignores words without digits and short numbers', () => {
    expect(extractPrintedTokens('TRANSFERENCIA ACME 12')).toEqual([])
  })

  test('removes trailing punctuation from the fragment', () => {
    const [token] = extractPrintedTokens('REF INV-778899.')
    expect(token.text).toBe('INV-778899')
  })

  test('also tries the number joined to a short prefix split by the bank', () => {
    const [token] = extractPrintedTokens('ABONO F 2024101126')
    expect(token.text).toBe('2024101126')
    expect(token.variants).toEqual(expect.arrayContaining(['2024101126', 'F2024101126']))
  })

  test('also tries a number split in two parts', () => {
    const [token] = extractPrintedTokens('INV 2024 00123')
    expect(token.variants).toContain('INV202400123')
    expect(token.variants).toContain('202400123')
  })

  test('returns no tokens for an empty concept', () => {
    expect(extractPrintedTokens('')).toEqual([])
  })
})

describe('highlightSegments', () => {
  test('splits the concept around matched tokens', () => {
    const segments = highlightSegments('PAGO F-100 ACME', [{ start: 5, end: 10 }])
    expect(segments).toEqual([
      { text: 'PAGO ', hit: false },
      { text: 'F-100', hit: true },
      { text: ' ACME', hit: false },
    ])
  })

  test('merges overlapping ranges', () => {
    const segments = highlightSegments('ABCDEF', [
      { start: 0, end: 3 },
      { start: 2, end: 4 },
    ])
    expect(segments).toEqual([
      { text: 'ABCD', hit: true },
      { text: 'EF', hit: false },
    ])
  })
})
