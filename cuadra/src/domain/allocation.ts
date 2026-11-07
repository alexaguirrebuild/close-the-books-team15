import { convert, isBalanced } from './money'
import type { Allocation, FxRate, Item } from './types'

export type TxnAmount = { amount: number; currency: string; date: string }

/** Credit notes subtract when netted against invoices and add when refunded alone. */
function contribution(item: Item, amount: number, allCreditNotes: boolean): number {
  const size = Math.abs(amount)
  return item.kind === 'CN' && !allCreditNotes ? -size : size
}

function isOnlyCreditNotes(items: Item[]): boolean {
  return items.length > 0 && items.every((i) => i.kind === 'CN')
}

/** Spreads the cash over the items, in the currency of each item and always positive. */
export function autoAllocate(items: Item[], txn: TxnAmount, rates: FxRate[]): Allocation[] {
  const allCreditNotes = isOnlyCreditNotes(items)
  // Credit notes free up room first, so invoices can absorb what they net out.
  const orderOf = (item: Item) => (item.kind === 'CN' && !allCreditNotes ? 0 : 1)
  const ordered = [...items].sort((a, b) => orderOf(a) - orderOf(b))
  let remaining = Math.abs(txn.amount)
  const amounts = new Map<string, number>()

  for (const item of ordered) {
    const available = Math.abs(item.openAmount)
    if (item.kind === 'CN' && !allCreditNotes) {
      const rate = convert(1, item.currency, txn.currency, txn.date, rates) ?? 1
      remaining += available * rate
      amounts.set(item.itemId, available)
      continue
    }
    const inItemCurrency =
      convert(remaining, txn.currency, item.currency, txn.date, rates) ?? remaining
    const applied = Math.min(available, Math.max(inItemCurrency, 0))
    const rate = convert(1, item.currency, txn.currency, txn.date, rates) ?? 1
    remaining -= applied * rate
    amounts.set(item.itemId, Math.round(applied * 100) / 100)
  }
  return items.map((item) => ({ itemId: item.itemId, amount: amounts.get(item.itemId) ?? 0, exception: null }))
}

type ReallocateInput = {
  current: Allocation[]
  nextIds: string[]
  items: Map<string, Item>
  txn: TxnAmount
  rates: FxRate[]
}

const CENT = 0.005

/**
 * Changing the selection must not undo the reviewer's work: a row they typed, one with an
 * exception, or one of an earlier phase stays as it is, and only the rest shares the remaining cash.
 */
export function reallocate({ current, nextIds, items, txn, rates }: ReallocateInput): Allocation[] {
  const loaded = current.flatMap((row) => items.get(row.itemId) ?? [])
  const automatic = new Map(autoAllocate(loaded, txn, rates).map((row) => [row.itemId, row.amount]))
  const isPinned = (row: Allocation) =>
    row.exception !== null || !items.has(row.itemId) || Math.abs(row.amount - (automatic.get(row.itemId) ?? 0)) > CENT
  const pinned = current.filter((row) => nextIds.includes(row.itemId) && isPinned(row))
  const pinnedIds = new Set(pinned.map((row) => row.itemId))
  const free = nextIds.filter((id) => !pinnedIds.has(id)).flatMap((id) => items.get(id) ?? [])
  const left = Math.max(computeResidual(txn, pinned, items, rates).residual, 0)
  const spread = autoAllocate(free, { ...txn, amount: left }, rates)
  const byId = new Map([...pinned, ...spread].map((row) => [row.itemId, row]))
  return nextIds.flatMap((id) => byId.get(id) ?? [])
}

export type ResidualResult = {
  residual: number
  applied: number
  balanced: boolean
  fxInvolved: boolean
  missingRate: boolean
  /** What stays unpaid in the selected invoices, in the currency of the movement. */
  openGap: number
}

/** Residual in the currency of the movement; positive means money left to apply. */
export function computeResidual(
  txn: TxnAmount,
  allocations: Allocation[],
  items: Map<string, Item>,
  rates: FxRate[],
): ResidualResult {
  const known = allocations.flatMap((a) => {
    const item = items.get(a.itemId)
    return item ? [{ allocation: a, item }] : []
  })
  const allCreditNotes = isOnlyCreditNotes(known.map((r) => r.item))
  let applied = 0
  let missingRate = false
  let fxInvolved = false
  let openGap = 0

  // An item of an earlier phase is not loaded: a return of it still counts, at its amount.
  for (const allocation of allocations) {
    if (!items.has(allocation.itemId)) applied += Math.abs(allocation.amount)
  }

  for (const { allocation, item } of known) {
    const value = contribution(item, allocation.amount, allCreditNotes)
    if (item.currency !== txn.currency) fxInvolved = true
    const converted = convert(value, item.currency, txn.currency, txn.date, rates)
    if (converted === null) missingRate = true
    else applied += converted
    const unpaid = Math.abs(item.openAmount) - Math.abs(allocation.amount)
    openGap += convert(Math.max(unpaid, 0), item.currency, txn.currency, txn.date, rates) ?? 0
  }

  const target = Math.abs(txn.amount)
  return {
    residual: target - applied,
    applied,
    balanced: !missingRate && isBalanced(target, applied),
    fxInvolved,
    missingRate,
    openGap,
  }
}
