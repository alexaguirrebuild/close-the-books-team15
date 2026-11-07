import { autoAllocate, type TxnAmount } from './allocation'
import { convert } from './money'
import type { Allocation, ExceptionCode, FxRate, Item } from './types'

/** POLICIES section 2: these clear the item in full, even though less or more cash arrived. */
const CLEARS_IN_FULL: readonly ExceptionCode[] = [
  'SHORT_DISCOUNT',
  'SHORT_BANK_CHARGES',
  'SHORT_WITHHOLDING',
  'FX_DIFFERENCE',
  'OVERPAYMENT',
]

export const clearsInFull = (code: ExceptionCode): boolean => CLEARS_IN_FULL.includes(code)

type ApplyInput = {
  rows: Allocation[]
  code: ExceptionCode | null
  /** Touch only this row; the cash is still worked out across all of them. */
  onlyItemId?: string
  items: Map<string, Item>
  txn: TxnAmount
  rates: FxRate[]
}

/**
 * The exception decides the amount, so the reviewer never has to work it out:
 * cleared in full means the open amount; anything else means the cash applied,
 * or less if the reviewer typed less.
 * Credit notes netted in a payment stay without exception at their full amount;
 * the only code they take is RETURNED, which covers every item of the operation.
 */
export function applyException({ rows, code, onlyItemId, items, txn, rates }: ApplyInput): Allocation[] {
  const known = rows.flatMap((row) => items.get(row.itemId) ?? [])
  const cash = new Map(autoAllocate(known, txn, rates).map((row) => [row.itemId, row.amount]))
  return rows.map((row) => {
    if (onlyItemId && row.itemId !== onlyItemId) return row
    const item = items.get(row.itemId)
    if (!item) return { ...row, exception: code }
    const open = Math.abs(item.openAmount)
    if (item.kind === 'CN') return { ...row, amount: open, exception: code === 'RETURNED' ? code : null }
    const applied = Math.min(row.amount, cash.get(row.itemId) ?? row.amount)
    return { ...row, amount: code && clearsInFull(code) ? open : applied, exception: code }
  })
}

export type PaymentTerm = { pct: number; days: number }

export type Suggestion = { code: ExceptionCode; why: string }

export type SuggestInput = {
  txn: TxnAmount
  rows: Item[]
  terms: Map<string, PaymentTerm>
  rates: FxRate[]
  /** Some selected item is already settled by another movement. */
  alreadySettled: boolean
  /** The amounts on screen do not add up to the movement, whatever the balances say. */
  unbalanced?: boolean
}

const DISCOUNT_GRACE_DAYS = 3
const DISCOUNT_ROUNDING = 0.02
const WITHHOLDING_RATES = [1.25, 1.5, 10]
const WITHHOLDING_MARGIN_PCT = 0.05
const BANK_CHARGES_MAX_EUR = 60
const BANK_CHARGES_MAX_SHARE = 0.02
const DAY_MS = 86_400_000
/** Above a cent the golden always names the difference, even inside the scorer's 0.5 % tolerance. */
const ROUNDING = 0.01 + 1e-9

const withinDays = (from: string, to: string, days: number) =>
  Date.parse(to) - Date.parse(from) <= days * DAY_MS

type Short = { shortfall: number; total: number; invoices: Item[]; txn: TxnAmount; terms: Map<string, PaymentTerm>; rates: FxRate[] }

/** POLICIES section 3: the discount applies to the total, within the terms plus three days of grace. */
function discountSuggestion({ shortfall, total, invoices, txn, terms }: Short): Suggestion | null {
  const term = invoices.map((item) => terms.get(item.terms)).find(Boolean)
  if (!term || !term.pct) return null
  if (Math.abs(shortfall - (total * term.pct) / 100) > DISCOUNT_ROUNDING * invoices.length) return null
  const inTime = invoices.every((item) => !item.issueDate || withinDays(item.issueDate, txn.date, term.days + DISCOUNT_GRACE_DAYS))
  return inTime
    ? { code: 'SHORT_DISCOUNT', why: `Descuento del ${term.pct} % dentro de plazo` }
    : { code: 'PARTIAL', why: `Descuento del ${term.pct} % fuera de plazo: no se acepta y la diferencia queda abierta` }
}

function withholdingSuggestion({ shortfall, total }: Short): Suggestion | null {
  const rate = WITHHOLDING_RATES.find((pct) => Math.abs((shortfall / total) * 100 - pct) <= WITHHOLDING_MARGIN_PCT)
  return rate ? { code: 'SHORT_WITHHOLDING', why: `Coincide con una retención del ${rate} %` } : null
}

function chargesSuggestion({ shortfall, total, txn, rates }: Short): Suggestion | null {
  const inEur = convert(shortfall, txn.currency, 'EUR', txn.date, rates) ?? shortfall
  if (inEur > BANK_CHARGES_MAX_EUR || shortfall > total * BANK_CHARGES_MAX_SHARE) return null
  return { code: 'SHORT_BANK_CHARGES', why: `Diferencia pequeña: hasta ${BANK_CHARGES_MAX_EUR} EUR y ${BANK_CHARGES_MAX_SHARE * 100} %` }
}

const STAYS_OPEN: Suggestion[] = [
  { code: 'PARTIAL', why: 'La partida queda con saldo abierto' },
  { code: 'SHORT_DISPUTE', why: 'El cliente descontó con un motivo, normalmente en su aviso de pago' },
]

const uniqueByCode = (list: Suggestion[]) => list.filter((s, i) => list.findIndex((o) => o.code === s.code) === i)

/** Best first. Only what POLICIES allows for this situation, each with its reason. */
export function suggestExceptions({ txn, rows, terms, rates, alreadySettled, unbalanced = false }: SuggestInput): Suggestion[] {
  if (!rows.length) return []
  const cash = Math.abs(txn.amount)
  const invoices = rows.filter((item) => item.kind !== 'CN')
  const value = (item: Item) => convert(Math.abs(item.openAmount), item.currency, txn.currency, txn.date, rates) ?? 0
  const total = invoices.reduce((sum, item) => sum + value(item), 0)
  const credits = rows.filter((item) => item.kind === 'CN').reduce((sum, item) => sum + value(item), 0)
  const shortfall = total - credits - cash
  const duplicate: Suggestion[] = alreadySettled ? [{ code: 'DUPLICATE_PAYMENT', why: 'La partida ya estaba liquidada por otro movimiento' }] : []

  if (invoices.some((item) => item.currency !== txn.currency)) {
    return [{ code: 'FX_DIFFERENCE', why: 'La factura está en otra divisa: el banco convierte a su cambio' }, ...STAYS_OPEN]
  }
  if (Math.abs(shortfall) <= ROUNDING) return unbalanced ? uniqueByCode([...duplicate, ...STAYS_OPEN]) : duplicate
  if (shortfall < 0) {
    return uniqueByCode([...duplicate, { code: 'OVERPAYMENT', why: 'Llegó más dinero que el saldo de la partida' }, ...STAYS_OPEN])
  }
  const short = { shortfall, total, invoices, txn, terms, rates }
  const matches = [discountSuggestion(short), withholdingSuggestion(short), chargesSuggestion(short)]
  return uniqueByCode([...duplicate, ...matches.filter((s): s is Suggestion => s !== null), ...STAYS_OPEN])
}
