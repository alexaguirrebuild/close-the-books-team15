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

export const SCHEMAS: Record<SchemaKey, Schema> = {
  bank: {
    fields: {
      id: ['bank_txn_id', 'txn_id', 'transaction_id', 'id'],
      date: ['date', 'booking_date', 'value_date', 'txn_date', 'transaction_date', 'posted_date', 'fecha', 'fecha_valor'],
      amount: ['amount', 'signed_amount', 'importe', 'txn_amount'],
      currency: CURRENCY,
      account: ['account', 'account_id', 'bank_account', 'iban', 'cuenta', 'account_number'],
      description: ['description', 'concept', 'concepto', 'bank_concept', 'concepto_bancario', 'narrative', 'memo', 'remittance_info', 'remittance', 'details', 'text', 'reference'],
    },
    required: ['id', 'date', 'amount', 'currency', 'account', 'description'],
  },
  invoice: {
    fields: {
      id: ['item_id', 'id'],
      printed: ['printed_number', 'printed_no', 'printed_id', 'number', 'invoice_number', 'document_number', 'doc_number', 'credit_note_number', 'numero', 'numero_impreso'],
      party: PARTY,
      partyName: PARTY_NAME,
      amount: ITEM_AMOUNT,
      openAmount: OPEN_AMOUNT,
      currency: CURRENCY,
      dueDate: ['due_date', 'vencimiento', 'maturity_date', 'duedate', 'fecha_vencimiento', 'date', 'issue_date', 'invoice_date'],
      terms: ['payment_terms', 'payment_term', 'terms', 'terms_id', 'payment_term_id', 'condiciones_pago'],
    },
    required: ['id', 'amount', 'currency'],
  },
  open: {
    fields: {
      id: ['item_id', 'id'],
      openAmount: [...OPEN_AMOUNT, 'amount'],
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
      reason: ['reason', 'motivo', 'why', 'explanation', 'rationale'],
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
    },
    required: ['id'],
  },
  fx: {
    fields: {
      date: ['date', 'rate_date', 'fecha'],
      base: ['base', 'base_currency', 'from', 'from_currency', 'currency_from', 'source_currency', 'currency'],
      quote: ['quote', 'quote_currency', 'to', 'to_currency', 'currency_to', 'target_currency'],
      rate: ['rate', 'fx_rate', 'exchange_rate', 'tipo', 'value'],
    },
    required: ['date', 'base', 'quote', 'rate'],
  },
  terms: {
    fields: {
      id: ['payment_terms', 'payment_term', 'terms', 'terms_id', 'payment_term_id', 'code', 'id'],
      discount: ['discount_pct', 'discount_percent', 'early_discount_pct', 'discount', 'descuento'],
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
  proposals: 'propuestas.csv',
  golden: 'golden.csv',
  categories: 'masters/categories.csv',
  customers: 'masters/customers.csv',
  suppliers: 'masters/suppliers.csv',
  fx: 'masters/fx_rates.csv',
  terms: 'masters/payment_terms.csv',
  pdfText: 'pdfs_text.csv',
}

const FILE_PATTERNS: [RegExp, FileRole][] = [
  [/^bank_transactions.*\.csv$/, 'bank'],
  [/^ar_invoices.*\.csv$/, 'ar'],
  [/^ap_invoices.*\.csv$/, 'ap'],
  [/^credit_notes.*\.csv$/, 'cn'],
  [/^open_items_at_start.*\.csv$/, 'open'],
  [/^(propuestas|proposals).*\.csv$/, 'proposals'],
  [/^.*(golden|ground_truth|gold).*\.csv$/, 'golden'],
  [/^categories.*\.csv$/, 'categories'],
  [/^customers.*\.csv$/, 'customers'],
  [/^suppliers.*\.csv$/, 'suppliers'],
  [/^fx_rates.*\.csv$/, 'fx'],
  [/^payment_terms.*\.csv$/, 'terms'],
  [/^pdfs_text.*\.csv$/, 'pdfText'],
]

export function classifyFile(path: string): FileRole | null {
  const name = (path.split('/').at(-1) ?? '').toLowerCase()
  return FILE_PATTERNS.find(([pattern]) => pattern.test(name))?.[1] ?? null
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
