import { describe, expect, test } from 'vitest'
import { rankCandidates, type RawCandidates } from './candidates'
import type { Item } from './types'

const makeItem = (over: Partial<Item>): Item => ({
  itemId: 'ARI-1',
  kind: 'AR',
  printed: 'F1',
  partyId: 'C1',
  partyName: 'Acme',
  amount: 100,
  openAmount: 100,
  currency: 'EUR',
  dueDate: '2024-01-01',
  isOpen: true,
  terms: '',
  ...over,
})

const txn = { amount: 100, currency: 'EUR', date: '2024-02-01' }

const raw = (over: Partial<RawCandidates>): RawCandidates => ({
  proposal: [],
  printed: [],
  exact: [],
  party: [],
  ...over,
})

const emptyUsed = new Map<string, string>()

describe('rankCandidates', () => {
  test('merges the reasons of an item found by several routes', () => {
    const item = makeItem({})
    const result = rankCandidates(
      txn,
      raw({ printed: [{ item, fragment: 'F1' }], exact: [item], party: [item] }),
      emptyUsed,
    )
    expect(result).toHaveLength(1)
    expect(result[0].reasons).toEqual(expect.arrayContaining(['printed', 'exact', 'party']))
    expect(result[0].fragment).toBe('F1')
  })

  test('ranks a printed number above a plain amount match', () => {
    const printed = makeItem({ itemId: 'ARI-P' })
    const exact = makeItem({ itemId: 'ARI-E', partyId: 'C2' })
    const result = rankCandidates(
      txn,
      raw({ printed: [{ item: printed, fragment: 'X' }], exact: [exact] }),
      emptyUsed,
    )
    expect(result[0].item.itemId).toBe('ARI-P')
  })

  test('marks the oldest of same party and amount as FIFO', () => {
    const older = makeItem({ itemId: 'ARI-OLD', dueDate: '2023-01-01' })
    const newer = makeItem({ itemId: 'ARI-NEW', dueDate: '2024-01-01' })
    const result = rankCandidates(txn, raw({ exact: [newer, older] }), emptyUsed)
    expect(result[0].item.itemId).toBe('ARI-OLD')
    expect(result[0].fifo).toBe(true)
    expect(result[1].fifo).toBe(false)
  })

  test('moves FIFO to the next item once the oldest is used', () => {
    const older = makeItem({ itemId: 'ARI-OLD', dueDate: '2023-01-01' })
    const newer = makeItem({ itemId: 'ARI-NEW', dueDate: '2024-01-01' })
    const used = new Map([['ARI-OLD', 'T9']])
    const result = rankCandidates(txn, raw({ exact: [older, newer] }), used)
    expect(result[0].item.itemId).toBe('ARI-NEW')
    expect(result[0].fifo).toBe(true)
    expect(result[1].usedBy).toBe('T9')
  })

  test('does not apply FIFO when the printed number points at one item', () => {
    const older = makeItem({ itemId: 'ARI-OLD', dueDate: '2023-01-01' })
    const newer = makeItem({ itemId: 'ARI-NEW', dueDate: '2024-01-01' })
    const result = rankCandidates(
      txn,
      raw({ exact: [older, newer], printed: [{ item: newer, fragment: 'F9' }] }),
      emptyUsed,
    )
    expect(result[0].item.itemId).toBe('ARI-NEW')
    expect(result.every((c) => !c.fifo)).toBe(true)
  })

  test('does not call a converted amount exact', () => {
    const result = rankCandidates(txn, raw({ exact: [makeItem({ currency: 'USD' })] }), emptyUsed)
    expect(result[0].reasons).toEqual(['converted'])
  })

  test('ranks an exact amount above a converted one', () => {
    const converted = makeItem({ itemId: 'ARI-USD', currency: 'USD', dueDate: '2020-01-01', partyId: 'C2' })
    const exact = makeItem({ itemId: 'ARI-EUR', partyId: 'C3' })
    const result = rankCandidates(txn, raw({ exact: [converted, exact] }), emptyUsed)
    expect(result[0].item.itemId).toBe('ARI-EUR')
  })

  test('sends closed items to the end', () => {
    const closed = makeItem({ itemId: 'ARI-C', isOpen: false, dueDate: '2020-01-01' })
    const open = makeItem({ itemId: 'ARI-O', partyId: 'C2' })
    const result = rankCandidates(txn, raw({ exact: [closed, open] }), emptyUsed)
    expect(result[0].item.itemId).toBe('ARI-O')
  })
})
