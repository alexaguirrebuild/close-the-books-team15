import { describe, expect, test } from 'vitest'
import { findCombos } from './combos'

const item = (id: string, value: number) => ({ id, value })

describe('findCombos', () => {
  test('finds a pair that adds up exactly', () => {
    const combos = findCombos([item('a', 100), item('b', 50), item('c', 30)], 150)
    expect(combos[0].ids).toEqual(['a', 'b'])
  })

  test('accepts a sum inside the tolerance', () => {
    const combos = findCombos([item('a', 600), item('b', 400)], 1004)
    expect(combos[0].ids).toEqual(['a', 'b'])
  })

  test('rejects a sum outside the tolerance', () => {
    expect(findCombos([item('a', 600), item('b', 400)], 1010)).toEqual([])
  })

  test('does not return single items', () => {
    expect(findCombos([item('a', 100), item('b', 7)], 100)).toEqual([])
  })

  test('nets a credit note against invoices', () => {
    const combos = findCombos([item('inv', 100), item('cn', -20), item('x', 500)], 80)
    expect(combos[0].ids).toEqual(['inv', 'cn'])
  })

  test('prefers the combination with fewer items', () => {
    const combos = findCombos(
      [item('a', 50), item('b', 25), item('c', 25), item('d', 100), item('e', 50)],
      150,
    )
    expect(combos[0].ids).toHaveLength(2)
  })

  test('respects the size limit', () => {
    const combos = findCombos(
      [item('a', 10), item('b', 20), item('c', 40), item('d', 80), item('e', 160)],
      310,
      { maxSize: 4 },
    )
    expect(combos).toEqual([])
  })

  test('prefers the closest sum among those of the same size', () => {
    const combos = findCombos([item('a', 600), item('b', 402), item('c', 400)], 1000)
    expect(combos[0].ids).toEqual(['a', 'c'])
  })

  test('stays bounded on a large party', () => {
    const many = Array.from({ length: 500 }, (_, i) => item(`i${i}`, 10 + i))
    const started = performance.now()
    findCombos(many, 99999)
    expect(performance.now() - started).toBeLessThan(200)
  })
})
