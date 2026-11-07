import { convert, isBalanced, tolerance } from './money'
import type { Allocation, ExceptionCode, FxRate, Item, ItemKind } from './types'

type TxnAmount = { amount: number; currency: string; date: string }

export function itemFromKind(itemId: string): ItemKind {
  if (itemId.startsWith('ARI')) return 'AR'
  if (itemId.startsWith('API')) return 'AP'
  if (itemId.startsWith('ARC')) return 'CN'
  return 'OTHER'
}

/** Credit notes subtract when netted against invoices and add when refunded alone. */
function contribution(item: Item, amount: number, allCreditNotes: boolean): number {
  const size = Math.abs(amount)
  return item.kind === 'CN' && !allCreditNotes ? -size : size
}

function isOnlyCreditNotes(items: Item[]): boolean {
  return items.length > 0 && items.every((i) => i.kind === 'CN')
}

export function autoAllocate(items: Item[], txn: TxnAmount, rates: FxRate[]): Allocation[] {
  const allCreditNotes = isOnlyCreditNotes(items)
  // Credit notes free up room first, so invoices can absorb what they net out.
  const orderOf = (item: Item) => (item.kind === 'CN' && !allCreditNotes ? 0 : 1)
  const ordered = [...items].sort((a, b) => orderOf(a) - orderOf(b))
  let remaining = Math.abs(txn.amount)
  const amounts = new Map<string, number>()

  for (const item of ordered) {
    const available = Math.abs(item.openAmount)
    const sign = item.openAmount < 0 ? -1 : 1
    if (item.kind === 'CN' && !allCreditNotes) {
      const rate = convert(1, item.currency, txn.currency, txn.date, rates) ?? 1
      remaining += available * rate
      amounts.set(item.itemId, sign * available)
      continue
    }
    const inItemCurrency =
      convert(remaining, txn.currency, item.currency, txn.date, rates) ?? remaining
    const applied = Math.min(available, Math.max(inItemCurrency, 0))
    const rate = convert(1, item.currency, txn.currency, txn.date, rates) ?? 1
    remaining -= applied * rate
    amounts.set(item.itemId, sign * Math.round(applied * 100) / 100)
  }
  return items.map((item) => ({ itemId: item.itemId, amount: amounts.get(item.itemId) ?? 0 }))
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
  const resolved = allocations.flatMap((a) => {
    const item = items.get(a.itemId)
    return item ? [{ allocation: a, item }] : []
  })
  const allCreditNotes = isOnlyCreditNotes(resolved.map((r) => r.item))
  let applied = 0
  let missingRate = false
  let fxInvolved = false
  let openGap = 0

  for (const { allocation, item } of resolved) {
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

type SuggestInput = {
  residual: number
  balanced: boolean
  fxInvolved: boolean
  txnAmount: number
  discountPct: number | null
  openGap?: number
}

export function suggestExceptions(input: SuggestInput): ExceptionCode[] {
  const { residual, balanced, fxInvolved, txnAmount, discountPct, openGap = 0 } = input
  const settled = openGap <= tolerance(txnAmount)
  if (balanced && settled) return []
  const shortfall = balanced ? openGap : -residual
  const base = Math.abs(txnAmount) + shortfall
  const fx: ExceptionCode[] = fxInvolved ? ['FX_DIFFERENCE'] : []
  if (!balanced && residual > 0) return [...fx, 'OVERPAYMENT', 'DUPLICATE_PAYMENT']
  const matchesDiscount =
    discountPct !== null && Math.abs(shortfall - (base * discountPct) / 100) <= tolerance(base)
  const short: ExceptionCode[] = [
    'SHORT_DISCOUNT',
    'SHORT_BANK_CHARGES',
    'SHORT_WITHHOLDING',
    'SHORT_DISPUTE',
    'PARTIAL',
  ]
  const ordered = matchesDiscount
    ? short
    : (['PARTIAL', 'SHORT_BANK_CHARGES', 'SHORT_WITHHOLDING', 'SHORT_DISCOUNT', 'SHORT_DISPUTE'] as ExceptionCode[])
  return [...fx, ...ordered]
}
