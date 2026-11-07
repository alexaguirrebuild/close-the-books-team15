import { computeResidual, type TxnAmount } from './allocation'
import { convert, formatMoney, tolerance } from './money'
import { EXCEPTION_LABELS, type Allocation, type ExceptionCode, type FxRate, type Item } from './types'

export type BalanceStatus = 'empty' | 'no_item' | 'short' | 'over' | 'balanced' | 'open_gap' | 'explained' | 'missing_rate'

export type BalanceSegment = { itemId: string; share: number }

export type Balance = {
  status: BalanceStatus
  title: string
  detail: string | null
  /** Invoices, each as a share of the bar. */
  segments: BalanceSegment[]
  /** How far the invoices reach along the bar. */
  fill: number
  /** Where they have to reach for the movement to add up. */
  notch: number
  /** Credit notes given back, as a share of the bar. */
  credit: number
}

type BalanceInput = {
  txn: TxnAmount
  rows: Allocation[]
  items: Map<string, Item>
  rates: FxRate[]
  /** Exception of a line without items. */
  exception: ExceptionCode | null
  category: string | null
  categoryLabel: string | null
}

const EMPTY_BAR = { segments: [], fill: 0, notch: 1, credit: 0 }
const CENT = 0.005

function geometry(txn: TxnAmount, rows: Allocation[], items: Map<string, Item>, rates: FxRate[]) {
  const target = Math.abs(txn.amount)
  const valued = rows.flatMap((row) => {
    const item = items.get(row.itemId)
    if (!item) return [{ itemId: row.itemId, value: Math.abs(row.amount), isCredit: false }]
    const value = Math.abs(convert(row.amount, item.currency, txn.currency, txn.date, rates) ?? 0)
    return [{ itemId: row.itemId, value, isCredit: item.kind === 'CN' }]
  })
  const onlyCredit = valued.length > 0 && valued.every((v) => v.isCredit)
  const invoices = valued.filter((v) => onlyCredit || !v.isCredit)
  const gross = invoices.reduce((sum, v) => sum + v.value, 0)
  const credit = onlyCredit ? 0 : valued.filter((v) => v.isCredit).reduce((sum, v) => sum + v.value, 0)
  const scale = Math.max(gross, target + credit, 1e-9)
  return {
    segments: invoices.map((v) => ({ itemId: v.itemId, share: v.value / scale })),
    fill: gross / scale,
    notch: (target + credit) / scale,
    credit: credit / scale,
  }
}

function noItemTitle({ exception, category, categoryLabel }: BalanceInput): string {
  if (category) return `Sin partida: ${categoryLabel || category}`
  return `Sin partida: ${exception ? EXCEPTION_LABELS[exception] : ''}`
}

/** What the bar and its sentence say about the line. The wording is the interface: keep it plain. */
export function computeBalance(input: BalanceInput): Balance {
  const { txn, rows, items, rates, exception, category } = input
  if (!rows.length) {
    if (category || exception) return { ...EMPTY_BAR, status: 'no_item', title: noItemTitle(input), detail: null }
    return { ...EMPTY_BAR, status: 'empty', title: 'Elige qué liquida este movimiento', detail: null }
  }
  const bar = geometry(txn, rows, items, rates)
  const residual = computeResidual(txn, rows, items, rates)
  const money = (value: number) => formatMoney(value, txn.currency)
  const gap = Math.abs(residual.residual)

  if (residual.missingRate) {
    return { ...bar, status: 'missing_rate', title: 'Falta el tipo de cambio de una divisa', detail: null }
  }
  const rowException = rows.find((row) => row.exception)?.exception
  if (rowException) {
    const difference = Math.max(gap, residual.openGap)
    const title = difference > CENT ? `Diferencia de ${money(difference)} explicada` : 'Cuadra con excepción'
    return { ...bar, status: 'explained', title, detail: EXCEPTION_LABELS[rowException] }
  }
  if (residual.balanced && residual.openGap > tolerance(residual.applied)) {
    return { ...bar, status: 'open_gap', title: `Cubre el movimiento, pero quedan ${money(residual.openGap)} por cobrar en la partida`, detail: null }
  }
  if (residual.balanced) {
    const title = gap > CENT ? `Cuadra, con ${money(gap)} dentro de la tolerancia` : 'Cuadra exacto'
    return { ...bar, status: 'balanced', title, detail: null }
  }
  return residual.residual > 0
    ? { ...bar, status: 'short', title: `Quedan ${money(gap)} por aplicar`, detail: null }
    : { ...bar, status: 'over', title: `Las partidas se pasan ${money(gap)}`, detail: null }
}
