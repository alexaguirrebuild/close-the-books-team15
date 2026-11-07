export const EXCEPTIONS = [
  'PARTIAL',
  'OVERPAYMENT',
  'SHORT_DISCOUNT',
  'SHORT_BANK_CHARGES',
  'SHORT_WITHHOLDING',
  'SHORT_DISPUTE',
  'DUPLICATE_PAYMENT',
  'UNIDENTIFIED',
  'RETURNED',
  'FX_DIFFERENCE',
] as const

export type ExceptionCode = (typeof EXCEPTIONS)[number]

export const EXCEPTION_LABELS: Record<ExceptionCode, string> = {
  PARTIAL: 'Pago parcial',
  OVERPAYMENT: 'Sobrepago',
  SHORT_DISCOUNT: 'Corto por descuento',
  SHORT_BANK_CHARGES: 'Corto por gastos bancarios',
  SHORT_WITHHOLDING: 'Corto por retención',
  SHORT_DISPUTE: 'Corto por disputa',
  DUPLICATE_PAYMENT: 'Pago duplicado',
  UNIDENTIFIED: 'Sin identificar',
  RETURNED: 'Devuelto',
  FX_DIFFERENCE: 'Diferencia de cambio',
}

export function isException(value: string): value is ExceptionCode {
  return (EXCEPTIONS as readonly string[]).includes(value)
}

export type ItemKind = 'AR' | 'AP' | 'CN' | 'OTHER'

export type Txn = {
  rid: number
  id: string
  date: string
  amount: number
  currency: string
  account: string
  description: string
  entity: string
  bank: string
  counterparty: string
  counterpartyAccount: string
  endToEnd: string
  remittance: string
  ref1: string
  ref2: string
  origAmount: number | null
  origCurrency: string
}

export type Item = {
  itemId: string
  kind: ItemKind
  printed: string
  partyId: string
  partyName: string
  amount: number
  openAmount: number
  currency: string
  dueDate: string
  issueDate: string
  /** False when the movement predates the item: nothing can settle it yet. */
  isOpen: boolean
  terms: string
  po: string
}

/** One row of the submission: the exception belongs to the row, not to the line. */
export type Allocation = { itemId: string; amount: number; exception: ExceptionCode | null }

export type DecisionSource = 'confirmed' | 'manual' | 'bulk' | 'imported'

export type Decision = {
  txnId: string
  allocations: Allocation[]
  category: string | null
  /** Only for a line without items; item rows carry their own in allocations. */
  exception: ExceptionCode | null
  source: DecisionSource
  at: number
  batch?: string
}

export type ProposalRow = {
  itemId: string | null
  amount: number | null
  exception: string | null
  category: string | null
  confidence: number | null
  reason: string
  source: string
  adviceId: string | null
  lineNo: string | null
}

export type FxRate = { date: string; base: string; quote: string; rate: number }

export type ExportRow = {
  bank_txn_id: string
  item_id: string
  amount: string
  category: string
  exception: string
}
