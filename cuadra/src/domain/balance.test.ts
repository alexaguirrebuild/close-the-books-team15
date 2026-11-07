import { describe, expect, test } from 'vitest'
import { computeBalance } from './balance'
import type { Allocation, Item } from './types'

const makeItem = (over: Partial<Item>): Item => ({
  itemId: 'INV',
  kind: 'AR',
  printed: 'F1',
  partyId: 'C1',
  partyName: 'Acme',
  amount: 100,
  openAmount: 100,
  currency: 'EUR',
  dueDate: '2024-03-01',
  issueDate: '2024-01-01',
  isOpen: true,
  terms: '',
  po: '',
  ...over,
})

const row = (itemId: string, amount: number, exception: Allocation['exception'] = null): Allocation => ({ itemId, amount, exception })
const txn = { amount: 100, currency: 'EUR', date: '2024-02-01' }
const items = (...list: Item[]) => new Map(list.map((item) => [item.itemId, item]))
const compute = (rows: Allocation[], map = items(makeItem({})), movement = txn) =>
  computeBalance({ txn: movement, rows, items: map, rates: [], exception: null, category: null, categoryLabel: null })

describe('computeBalance', () => {
  test('asks for a decision when nothing is chosen', () => {
    const balance = compute([])
    expect(balance.status).toBe('empty')
    expect(balance.segments).toEqual([])
    expect(balance.title).toBe('Elige qué liquida este movimiento')
  })

  test('closes when the items add up', () => {
    const balance = compute([row('INV', 100)])
    expect(balance.status).toBe('balanced')
    expect(balance.title).toBe('Cuadra exacto')
    expect(balance.fill).toBeCloseTo(1)
    expect(balance.notch).toBeCloseTo(1)
  })

  test('says it adds up within the tolerance when there is a small gap', () => {
    const balance = compute([row('INV', 99.8)], items(makeItem({ openAmount: 99.8 })))
    expect(balance.status).toBe('balanced')
    expect(balance.title).toContain('0,20')
  })

  test('says how much of the movement is left to apply', () => {
    const balance = compute([row('INV', 60)], items(makeItem({ openAmount: 60 })))
    expect(balance.status).toBe('short')
    expect(balance.title).toBe('Quedan 40,00 EUR por aplicar')
    expect(balance.fill).toBeCloseTo(0.6)
    expect(balance.notch).toBeCloseTo(1)
  })

  test('says how much the items go over the movement', () => {
    const balance = compute([row('INV', 130)], items(makeItem({ openAmount: 130 })))
    expect(balance.status).toBe('over')
    expect(balance.title).toBe('Las partidas se pasan 30,00 EUR')
    expect(balance.fill).toBeCloseTo(1)
    expect(balance.notch).toBeCloseTo(100 / 130)
  })

  test('warns when the movement is covered but the invoice stays open', () => {
    const balance = compute([row('INV', 100)], items(makeItem({ openAmount: 250 })))
    expect(balance.status).toBe('open_gap')
    expect(balance.title).toContain('150,00 EUR')
  })

  test('gives each item a share of the bar', () => {
    const map = items(makeItem({ itemId: 'A', openAmount: 25 }), makeItem({ itemId: 'B', openAmount: 75 }))
    const balance = compute([row('A', 25), row('B', 75)], map)
    expect(balance.segments.map((s) => s.share)).toEqual([0.25, 0.75])
  })

  test('shows a credit note as what the invoices give back', () => {
    const map = items(makeItem({ itemId: 'A', openAmount: 120 }), makeItem({ itemId: 'CN', kind: 'CN', openAmount: 20 }))
    const balance = compute([row('A', 120), row('CN', 20)], map)
    expect(balance.status).toBe('balanced')
    expect(balance.credit).toBeCloseTo(20 / 120)
    expect(balance.fill).toBeCloseTo(1)
    expect(balance.notch).toBeCloseTo(1)
  })

  test('explains the difference when the row carries an exception', () => {
    const map = items(makeItem({ openAmount: 102 }))
    const balance = compute([row('INV', 102, 'SHORT_DISCOUNT')], map)
    expect(balance.status).toBe('explained')
    expect(balance.title).toBe('Diferencia de 2,00 EUR explicada')
    expect(balance.detail).toBe('Corto por descuento')
  })

  test('keeps a partial payment as explained too', () => {
    const balance = compute([row('INV', 100, 'PARTIAL')], items(makeItem({ openAmount: 250 })))
    expect(balance.status).toBe('explained')
    expect(balance.detail).toBe('Pago parcial')
  })

  test('reports a currency without a rate instead of a wrong figure', () => {
    const balance = compute([row('INV', 100)], items(makeItem({ currency: 'USD' })))
    expect(balance.status).toBe('missing_rate')
  })

  test('describes a line without items by its category', () => {
    const balance = computeBalance({ txn, rows: [], items: new Map(), rates: [], exception: null, category: 'BANK_FEES', categoryLabel: 'Comisiones' })
    expect(balance.status).toBe('no_item')
    expect(balance.title).toBe('Sin partida: Comisiones')
  })
})
