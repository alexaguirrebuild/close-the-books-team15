export type SchemaKey =
  | 'bank'
  | 'invoice'
  | 'open'
  | 'proposals'
  | 'golden'
  | 'categories'
  | 'party'
  | 'fx'
  | 'terms'
  | 'pdfText'

export type FileRole =
  | 'bank'
  | 'ar'
  | 'ap'
  | 'cn'
  | 'open'
  | 'proposals'
  | 'golden'
  | 'categories'
  | 'customers'
  | 'suppliers'
  | 'fx'
  | 'terms'
  | 'pdfText'

export const ROLE_SCHEMA: Record<FileRole, SchemaKey> = {
  bank: 'bank',
  ar: 'invoice',
  ap: 'invoice',
  cn: 'invoice',
  open: 'open',
  proposals: 'proposals',
  golden: 'golden',
  categories: 'categories',
  customers: 'party',
  suppliers: 'party',
  fx: 'fx',
  terms: 'terms',
  pdfText: 'pdfText',
}

type Schema = { fields: Record<string, string[]>; required: string[] }

const ITEM_AMOUNT = ['amount', 'total', 'total_amount', 'gross_amount', 'importe', 'invoice_amount', 'amount_total']
const OPEN_AMOUNT = ['open_amount', 'amount_open', 'outstanding', 'outstanding_amount', 'remaining', 'balance', 'open_balance', 'saldo', 'importe_abierto']
const PARTY = ['party_id', 'customer_id', 'supplier_id', 'vendor_id', 'counterparty_id', 'third_party_id', 'tercero_id', 'party', 'customer', 'supplier', 'vendor', 'tercero', 'counterparty']
const PARTY_NAME = ['party_name', 'customer_name', 'supplier_name', 'vendor_name', 'counterparty_name', 'tercero_nombre', 'name', 'nombre']
const CURRENCY = ['currency', 'ccy', 'currency_code', 'divisa', 'moneda']
const PRINTED = ['printed_number', 'printed_no', 'printed_id', 'number', 'invoice_number', 'supplier_invoice_number', 'document_number', 'doc_number', 'credit_note_number', 'numero', 'numero_impreso']
const DUE_DATE = ['due_date', 'vencimiento', 'maturity_date', 'duedate', 'fecha_vencimiento']
const ISSUE_DATE = ['issue_date', 'invoice_date', 'document_date', 'date']
const TERMS = ['payment_terms', 'payment_term', 'terms', 'terms_id', 'payment_term_id', 'condiciones_pago']

export const SCHEMAS: Record<SchemaKey, Schema> = {
  bank: {
    fields: {
      id: ['bank_txn_id', 'txn_id', 'transaction_id', 'id'],
      date: ['date', 'booking_date', 'value_date', 'txn_date', 'transaction_date', 'posted_date', 'fecha', 'fecha_valor'],
      amount: ['amount', 'signed_amount', 'importe', 'txn_amount'],
      currency: CURRENCY,
      account: ['account', 'account_id', 'bank_account', 'iban', 'cuenta', 'account_number'],
      description: ['description', 'concept', 'concepto', 'bank_concept', 'concepto_bancario', 'narrative', 'memo', 'text', 'details', 'reference', 'remittance_info', 'remittance'],
      entity: ['entity', 'company', 'sociedad'],
      bank: ['bank', 'banco'],
      counterparty: ['counterparty_name', 'counterparty', 'ordenante'],
      counterpartyAccount: ['counterparty_account', 'counterparty_iban'],
      endToEnd: ['end_to_end_id', 'endtoendid'],
      remittance: ['remittance_info', 'remittance'],
      ref1: ['ref1'],
      ref2: ['ref2'],
      origAmount: ['orig_amount', 'instructed_amount'],
      origCurrency: ['orig_currency', 'instructed_currency'],
    },
    required: ['id', 'date', 'amount', 'currency', 'account', 'description'],
  },
  invoice: {
    fields: {
      id: ['item_id', 'id'],
      printed: PRINTED,
      party: PARTY,
      partyName: PARTY_NAME,
      amount: ITEM_AMOUNT,
      openAmount: OPEN_AMOUNT,
      currency: CURRENCY,
      dueDate: DUE_DATE,
      issueDate: ISSUE_DATE,
      terms: TERMS,
      po: ['customer_po', 'our_po', 'po', 'po_number', 'pedido'],
    },
    required: ['id', 'amount', 'currency'],
  },
  open: {
    fields: {
      id: ['item_id', 'id'],
      openAmount: [...OPEN_AMOUNT, 'amount'],
      printed: PRINTED,
      party: PARTY,
      amount: ITEM_AMOUNT,
      currency: CURRENCY,
      dueDate: DUE_DATE,
      issueDate: ISSUE_DATE,
    },
    required: ['id'],
  },
  proposals: {
    fields: {
      txn: ['bank_txn_id', 'txn_id'],
      item: ['item_id'],
      amount: ['amount', 'suggested_amount', 'amount_suggested', 'amount_sugerido', 'importe'],
      exception: ['exception', 'suggested_exception', 'exception_suggested', 'exception_sugerida', 'excepcion'],
      category: ['category', 'categoria'],
      confidence: ['confidence', 'confianza', 'score', 'conf'],
      reason: ['reason', 'motivo', 'why', 'explanation', 'rationale', 'evidence', 'evidencia'],
      source: ['source', 'fuente', 'stage', 'rule', 'regla'],
      adviceId: ['advice_id'],
      lineNo: ['line_no'],
    },
    required: ['txn'],
  },
  golden: {
    fields: {
      txn: ['bank_txn_id', 'txn_id'],
      item: ['item_id'],
      amount: ['amount'],
      category: ['category'],
      exception: ['exception'],
      segment: ['segment'],
      difficulty: ['difficulty'],
    },
    required: ['txn'],
  },
  categories: {
    fields: {
      category: ['category', 'code', 'category_code', 'categoria', 'name', 'id'],
      label: ['label', 'description', 'descripcion', 'category_name'],
    },
    required: ['category'],
  },
  party: {
    fields: {
      id: PARTY,
      name: PARTY_NAME,
      tradeName: ['trade_name'],
      iban: ['iban'],
      entity: ['entity'],
      terms: TERMS,
    },
    required: ['id'],
  },
  fx: {
    fields: {
      date: ['date', 'rate_date', 'fecha'],
      base: ['base', 'base_currency', 'from', 'from_currency', 'currency_from', 'source_currency', 'currency'],
      quote: ['quote', 'quote_currency', 'to', 'to_currency', 'currency_to', 'target_currency'],
      rate: ['rate', 'fx_rate', 'exchange_rate', 'tipo', 'value', 'units_per_eur'],
    },
    // Without a quote column, rate means units of that currency per euro.
    required: ['date', 'base', 'rate'],
  },
  terms: {
    fields: {
      id: ['payment_terms', 'payment_term', 'terms', 'terms_id', 'payment_term_id', 'code', 'id'],
      discount: ['discount_pct', 'discount_percent', 'early_discount_pct', 'early_payment_discount_pct', 'discount', 'descuento'],
      discountDays: ['discount_days', 'early_payment_days'],
    },
    required: ['id'],
  },
  pdfText: {
    fields: {
      file: ['file', 'path', 'pdf'],
      page: ['page', 'pagina'],
      text: ['text', 'texto', 'content'],
    },
    required: ['file', 'text'],
  },
}

export const ROLE_LABEL: Record<FileRole, string> = {
  bank: 'bank_transactions.csv',
  ar: 'ar_invoices.csv',
  ap: 'ap_invoices.csv',
  cn: 'credit_notes.csv',
  open: 'open_items_at_start.csv',
  proposals: 'Propuestas',
  golden: 'golden_allocations.csv',
  categories: 'masters/categories.csv',
  customers: 'masters/customers.csv',
  suppliers: 'masters/suppliers.csv',
  fx: 'masters/fx_rates.csv',
  terms: 'masters/payment_terms.csv',
  pdfText: 'pdfs_text.csv',
}

const CSV = String.raw`\.csv(\.gz)?$`
const pattern = (start: string) => new RegExp(`^${start}.*${CSV}`)

const FILE_PATTERNS: [RegExp, FileRole][] = [
  [pattern('bank_transactions'), 'bank'],
  [pattern('ar_invoices'), 'ar'],
  [pattern('ap_invoices'), 'ap'],
  [pattern('credit_notes'), 'cn'],
  [pattern('open_items_at_start'), 'open'],
  [pattern('(propuestas|proposals)'), 'proposals'],
  [pattern('.*(golden|ground_truth|gold)'), 'golden'],
  [pattern('categories'), 'categories'],
  [pattern('customers'), 'customers'],
  [pattern('suppliers'), 'suppliers'],
  [pattern('fx_rates'), 'fx'],
  [pattern('payment_terms'), 'terms'],
  [pattern('pdfs_text'), 'pdfText'],
]

const UNDER_OUTPUT = /(^|\/)out\//

export const isCsvName = (name: string): boolean => new RegExp(CSV, 'i').test(name)

export function classifyFile(path: string): FileRole | null {
  const name = (path.split('/').at(-1) ?? '').toLowerCase()
  const role = FILE_PATTERNS.find(([regex]) => regex.test(name))?.[1]
  if (role) return role
  return UNDER_OUTPUT.test(path) && isCsvName(name) ? 'proposals' : null
}

export type ColumnMapping = Record<string, string | undefined>

export function resolveColumns(schemaKey: SchemaKey, headers: string[]): ColumnMapping {
  const byLower = new Map(headers.map((h) => [h.trim().toLowerCase(), h]))
  const taken = new Set<string>()
  const mapping: ColumnMapping = {}
  for (const [field, aliases] of Object.entries(SCHEMAS[schemaKey].fields)) {
    const alias = aliases.find((a) => {
      const header = byLower.get(a)
      return header !== undefined && !taken.has(header)
    })
    if (alias === undefined) continue
    const header = byLower.get(alias) as string
    mapping[field] = header
    taken.add(header)
  }
  return mapping
}

export function isCompleteMapping(schemaKey: SchemaKey, mapping: ColumnMapping): boolean {
  return SCHEMAS[schemaKey].required.every((field) => Boolean(mapping[field]))
}
