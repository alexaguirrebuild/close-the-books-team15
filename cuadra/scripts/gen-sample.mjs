#!/usr/bin/env node
// Generates a synthetic dataset with the exact columns and formats of the real data, so the app
// and its tests exercise the same path. Plain CSV: the team's pre-commit refuses any .csv.gz.
// Never use it for a real submission.
// Usage: node scripts/gen-sample.mjs --out public/sample/train --txns 3000 --items 12000 [--golden] [--seed 42]
import { createWriteStream, mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { once } from 'node:events'

const args = process.argv.slice(2)
const flag = (name, fallback) => {
  const at = args.indexOf(`--${name}`)
  return at < 0 ? fallback : args[at + 1]
}
const OUT = flag('out', 'public/sample/train')
const TXNS = Number(flag('txns', 3000))
const ITEMS = Math.max(Number(flag('items', 12000)), TXNS * 2)
const GOLDEN = args.includes('--golden')
let seed = Number(flag('seed', 42))

const random = () => {
  seed = (seed * 1664525 + 1013904223) % 4294967296
  return seed / 4294967296
}
const pick = (list) => list[Math.floor(random() * list.length)]
const between = (low, high) => low + random() * (high - low)
const money = (value) => (Math.round(value * 100) / 100).toFixed(2)
const round2 = (value) => Math.round(value * 100) / 100
const csv = (value) => {
  const text = value === null || value === undefined ? '' : String(value)
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

class Writer {
  constructor(path, header) {
    this.stream = createWriteStream(path)
    this.stream.setMaxListeners(0)
    this.write(header)
  }
  write(cells) {
    if (!this.stream.write(`${cells.map(csv).join(',')}\n`)) this.pending = once(this.stream, 'drain')
  }
  async flush() {
    if (this.pending) {
      await this.pending
      this.pending = null
    }
  }
  close() {
    this.stream.end()
    return once(this.stream, 'finish')
  }
}

const dir = (...parts) => {
  const path = join(OUT, ...parts)
  mkdirSync(path, { recursive: true })
  return path
}
dir()
dir('masters')
dir('pdfs')

const NAME_A = ['Acero', 'Bosque', 'Cumbre', 'Delta', 'Estela', 'Faro', 'Granito', 'Horizonte', 'Ibérica', 'Jara', 'Kora', 'Lumen', 'Marea', 'Norte', 'Olivo', 'Prisma', 'Quanta', 'Roble', 'Sierra', 'Trama', 'Ulises', 'Vega', 'Zenit']
const NAME_B = ['Logística', 'Piscinas', 'Riegos', 'Montajes', 'Química', 'Transportes', 'Suministros', 'Ingeniería', 'Mantenimientos', 'Envases']
const FORM = ['S.A.', 'S.L.', 'S.L.U.', 'S.Coop.']
const CURRENCIES = ['EUR', 'EUR', 'EUR', 'EUR', 'USD', 'GBP']
const ACCOUNTS = {
  EUR: [['ES00-BBVA-EUR', 'ES00', 'BBVA'], ['ES00-CAIX-EUR', 'ES00', 'CAIX'], ['ES01-SANT-EUR', 'ES01', 'SANT']],
  USD: [['US01-CITI-USD', 'US01', 'CITI']],
  GBP: [['GB01-NWBK-GBP', 'GB01', 'NWBK']],
}
const CATEGORIES = [
  ['BANK_FEES', 'Comisiones y gastos bancarios', '626', 'COMISION MANTENIMIENTO CUENTA'],
  ['INTEREST_INCOME', 'Intereses a favor', '769', 'LIQUIDACION INTERESES ACREEDORES'],
  ['INTEREST_EXPENSE', 'Intereses de deudas o descubiertos', '662', 'LIQUIDACION INTERESES DEUDORES'],
  ['PAYROLL', 'Nóminas (remesa de transferencias)', '640', 'NOMINAS REMESA MENSUAL'],
  ['SOCIAL_SECURITY', 'Seguridad social y cargas sociales', '642', 'TGSS SEGUROS SOCIALES'],
  ['TAX_PAYMENT', 'Impuestos', '475', 'AEAT MODELO 303'],
  ['TAX_REFUND', 'Devolución de impuestos', '470', 'AEAT DEVOLUCION IVA'],
  ['LOAN_DRAWDOWN', 'Disposición de préstamo o póliza', '520', 'DISPOSICION POLIZA CREDITO'],
  ['LOAN_REPAYMENT', 'Cuota de préstamo', '170', 'CUOTA PRESTAMO'],
  ['LEASING', 'Cuota de leasing o renting', '621', 'RECIBO LEASING'],
  ['FX_DEAL', 'Compraventa de divisa', '768', 'LIQUIDACION FORWARD'],
  ['CASH_POOL', 'Barrido de cash pooling', '551', 'ZBA SWEEP TO HEADER'],
  ['INTERCOMPANY', 'Transferencia intragrupo', '552', 'TRANSFERENCIA INTRAGRUPO'],
  ['INTERNAL_TRANSFER', 'Traspaso entre cuentas propias', '572', 'TRASPASO ENTRE CUENTAS'],
  ['CUSTOMER_ADVANCE', 'Anticipo de cliente sin factura', '438', 'ANTICIPO PROFORMA'],
  ['CUSTOMER_REFUND', 'Devolución de dinero a cliente', '438', 'DEVOLUCION A CLIENTE'],
  ['SUPPLIER_REFUND', 'Reembolso de proveedor', '407', 'REEMBOLSO PROVEEDOR'],
  ['CARD_EXPENSES', 'Liquidación de tarjetas de empresa', '410', 'LIQUIDACION TARJETAS EMPRESA'],
  ['INSURANCE', 'Primas de seguros', '625', 'PRIMA SEGURO'],
  ['BANK_REVERSAL', 'Apunte erróneo del banco y su anulación', '572', 'ANULACION OPERACION INDEBIDA'],
  ['UNIDENTIFIED', 'Cobro no identificado', '555', ''],
]
const EUR_PER_UNIT = { USD: 0.92, GBP: 1.17, EUR: 1 }
const NO_ITEM_CATEGORIES = CATEGORIES.filter(([code]) => code !== 'UNIDENTIFIED')

const partyCount = Math.max(40, Math.round(ITEMS / 60))
const iban = (i) => `ES${String(10 + (i % 89))}${String(i).padStart(20, '0')}`
const uniqueName = (i) => `${pick(NAME_A)} ${pick(NAME_B)} ${i} ${pick(FORM)}`
const makeParty = (prefix, i) => ({ id: `${prefix}ES00${String(i).padStart(6, '0')}`, name: uniqueName(i), iban: iban(i) })
const customers = Array.from({ length: partyCount }, (_, i) => makeParty('C', i))
const suppliers = Array.from({ length: Math.round(partyCount / 2) }, (_, i) => makeParty('S', partyCount + i))

const dateOf = (dayOffset) => new Date(Date.UTC(2024, 0, 1 + dayOffset)).toISOString().slice(0, 10)
const yearOf = (dayOffset) => dateOf(dayOffset).slice(0, 4)

const ar = new Writer(join(OUT, 'ar_invoices.csv'), ['item_id', 'entity', 'document_number', 'issue_date', 'due_date', 'customer_id', 'customer_name', 'currency', 'net_amount', 'tax_amount', 'total_amount', 'payment_terms', 'customer_po', 'payment_method'])
const ap = new Writer(join(OUT, 'ap_invoices.csv'), ['item_id', 'entity', 'supplier_id', 'supplier_name', 'supplier_invoice_number', 'issue_date', 'due_date', 'currency', 'net_amount', 'tax_amount', 'total_amount', 'payment_terms', 'our_po', 'payment_method'])
const cn = new Writer(join(OUT, 'credit_notes.csv'), ['item_id', 'side', 'entity', 'document_number', 'issue_date', 'party_id', 'party_name', 'currency', 'net_amount', 'tax_amount', 'total_amount', 'related_document', 'reason'])
const open = new Writer(join(OUT, 'open_items_at_start.csv'), ['item_id', 'item_type', 'entity', 'party_id', 'document_number', 'issue_date', 'due_date', 'currency', 'total_amount', 'open_amount'])
const bank = new Writer(join(OUT, 'bank_transactions.csv'), ['bank_txn_id', 'account_id', 'entity', 'bank', 'statement_format', 'statement_file', 'booking_date', 'value_date', 'amount', 'currency', 'bank_code', 'bank_subcode', 'ref1', 'ref2', 'text', 'counterparty_name', 'counterparty_account', 'end_to_end_id', 'remittance_info', 'orig_amount', 'orig_currency'])
const proposals = new Writer(join(OUT, 'propuestas.csv'), ['bank_txn_id', 'item_id', 'amount', 'category', 'exception', 'source', 'confidence', 'reason'])
const golden = GOLDEN ? new Writer(join(OUT, 'golden_allocations.csv'), ['bank_txn_id', 'item_id', 'amount', 'category', 'exception', 'segment', 'difficulty']) : null

const NET_SHARE = 0.82
let itemSeq = 0
const nextItem = (kind, party, amount, currency, dayOffset, terms = 'T30') => {
  itemSeq += 1
  const year = yearOf(dayOffset)
  const seq = String(itemSeq).padStart(7, '0')
  const prefix = { ar: 'ARI', ap: 'API', cn: 'ARC' }[kind]
  const printed = { ar: `FV${year.slice(2)}-${seq}`, ap: `${year}/${seq}`, cn: `R${year.slice(2)}${seq}` }[kind]
  const total = round2(Math.abs(amount))
  const net = round2(total * NET_SHARE)
  const item = { id: `${prefix}-${year}-${seq}`, printed, party, amount: total, currency, kind }
  const issue = dateOf(dayOffset)
  const due = dateOf(dayOffset + 30)
  if (kind === 'ar') ar.write([item.id, 'ES00', printed, issue, due, party.id, party.name, currency, money(net), money(total - net), money(total), terms, '', 'transfer'])
  if (kind === 'ap') ap.write([item.id, 'ES00', party.id, party.name, printed, issue, due, currency, money(net), money(total - net), money(total), terms, '', 'transfer'])
  if (kind === 'cn') cn.write([item.id, 'AR', 'ES00', printed, issue, party.id, party.name, currency, money(net), money(total - net), money(total), '', 'Abono'])
  return item
}

/** Items from before the phase, known only through the open file. */
const openFromBefore = (party, amount, currency, dayOffset) => {
  itemSeq += 1
  const year = '2023'
  const seq = String(itemSeq).padStart(7, '0')
  const item = { id: `ARI-${year}-${seq}`, printed: `FV23-${seq}`, party, amount: round2(amount), currency, kind: 'ar' }
  open.write([item.id, 'ARI', 'ES00', party.id, item.printed, dateOf(dayOffset), dateOf(dayOffset + 30), currency, money(item.amount), money(item.amount)])
  return item
}

const amountFor = () => (random() < 0.2 ? between(5000, 90000) : between(40, 4800))
const cut = (text, length = 38) => text.slice(0, length).trim()
const nameInText = (party) => cut(party.name.toUpperCase().replace(/[.,]/g, ''), 30)
const conceptWithName = (party) => `TRANSFERENCIAS ${nameInText(party)}`
const conceptWithNumber = (item) => pick([`PAGO FRA ${item.printed} ${nameInText(item.party)}`, `REF ${item.printed}`, `ABONO ${item.printed.replace('-', ' ')}`])
const before = (day, low = 3, high = 40) => day - Math.floor(between(low, high))

const SCENARIOS = [
  ['printed', 30],
  ['exact_party', 14],
  ['fifo', 5],
  ['one_to_n', 9],
  ['from_before', 4],
  ['credit_note', 3],
  ['partial', 4],
  ['discount', 3],
  ['bank_charges', 3],
  ['withholding', 2],
  ['dispute', 3],
  ['overpayment', 2],
  ['fx', 5],
  ['duplicate', 1],
  ['returned', 2],
  ['category', 7],
  ['unidentified', 3],
]
const totalWeight = SCENARIOS.reduce((sum, [, weight]) => sum + weight, 0)
const pickScenario = () => {
  let roll = random() * totalWeight
  for (const [name, weight] of SCENARIOS) {
    roll -= weight
    if (roll <= 0) return name
  }
  return 'printed'
}

const accountFor = (currency) => pick(ACCOUNTS[currency])
const SHORT = {
  discount: { exception: 'SHORT_DISCOUNT', share: 0.02, terms: 'D2N30', hint: 'PRONTO PAGO', segment: 'ar_short_discount', days: [2, 9] },
  bank_charges: { exception: 'SHORT_BANK_CHARGES', share: 0.015, terms: 'T30', hint: 'GASTOS SHA', segment: 'ar_bank_charges', days: [3, 40] },
  withholding: { exception: 'SHORT_WITHHOLDING', share: 0.1, terms: 'T30', hint: 'RETENCION', segment: 'ar_withholding', days: [3, 40] },
}

/**
 * Returns the bank movement, its true rows and what the pipeline would propose.
 * The amounts follow POLICIES: an accepted short payment clears the whole invoice,
 * a dispute or a partial payment clears what was paid.
 */
function makeCase(index) {
  const scenario = pickScenario()
  const incoming = random() < 0.72
  const party = incoming ? pick(customers) : pick(suppliers)
  const kind = incoming ? 'ar' : 'ap'
  const sign = incoming ? 1 : -1
  const currency = pick(CURRENCIES)
  const day = Math.floor(between(45, 300))
  const row = (item, amount, exception = '') => ({ item: item.id, amount, exception, category: '' })
  const noItem = (category, exception = '') => ({ item: '', amount: null, exception, category })
  const withName = random() < 0.35 ? party : null
  const base = { currency, day, payer: withName }

  switch (scenario) {
    case 'printed': {
      const item = nextItem(kind, party, amountFor(), currency, before(day))
      const truth = [row(item, item.amount)]
      return { ...base, amount: sign * item.amount, concept: conceptWithNumber(item), truth, proposal: truth, conf: between(0.92, 0.999), why: 'Número impreso en el concepto e importe exacto', source: 'm1_reference', segment: 'ar_ref_noise', difficulty: 1 }
    }
    case 'exact_party': {
      const item = nextItem(kind, party, amountFor(), currency, before(day))
      const truth = [row(item, item.amount)]
      return { ...base, amount: sign * item.amount, concept: conceptWithName(party), truth, proposal: truth, conf: between(0.75, 0.93), why: 'Mismo tercero e importe exacto', source: 'm1_party', segment: 'ar_1to1', difficulty: 1 }
    }
    case 'fifo': {
      const amount = amountFor()
      const older = nextItem(kind, party, amount, currency, day - 44)
      const newer = nextItem(kind, party, amount, currency, day - 4)
      const wrong = random() < 0.3
      return { ...base, amount: sign * older.amount, concept: conceptWithName(party), truth: [row(older, older.amount)], proposal: [row(wrong ? newer : older, older.amount)], conf: between(0.55, 0.8), why: 'Varias partidas iguales del mismo tercero', source: 'm1_party', segment: 'ar_same_amount_fifo', difficulty: 2 }
    }
    case 'one_to_n': {
      const size = 2 + Math.floor(random() * 3)
      const items = Array.from({ length: size }, (_, i) => nextItem(kind, party, between(40, 3000), currency, before(day) - i * 7))
      const total = items.reduce((sum, item) => sum + item.amount, 0)
      const truth = items.map((item) => row(item, item.amount))
      const complete = random() < 0.7
      return { ...base, amount: sign * round2(total), concept: conceptWithName(party), truth, proposal: complete ? truth : truth.slice(0, 1), conf: complete ? between(0.6, 0.88) : between(0.3, 0.55), why: complete ? 'La combinación suma el importe' : 'Coincide el tercero, combinación incompleta', source: 'm2_remittance', segment: 'ar_1toN_text', difficulty: 2 }
    }
    case 'from_before': {
      const customer = pick(customers)
      const item = openFromBefore(customer, amountFor(), currency, -120)
      const truth = [row(item, item.amount)]
      return { ...base, payer: null, amount: item.amount, concept: conceptWithNumber(item), truth, proposal: truth, conf: between(0.8, 0.97), why: 'Partida abierta al empezar la fase', source: 'm1_reference', segment: 'ar_ref_noise', difficulty: 2 }
    }
    case 'credit_note': {
      const customer = customers[index % customers.length]
      const invoice = nextItem('ar', customer, between(300, 4000), currency, before(day))
      const note = nextItem('cn', customer, invoice.amount * between(0.05, 0.25), currency, before(day))
      const truth = [row(invoice, invoice.amount), row(note, note.amount)]
      return { ...base, payer: null, amount: round2(invoice.amount - note.amount), concept: conceptWithName(customer), truth, proposal: truth, conf: between(0.6, 0.85), why: 'Factura neta de abono', source: 'm1_party', segment: 'ar_1toN_credit_note', difficulty: 2 }
    }
    case 'partial': {
      const item = nextItem(kind, party, amountFor(), currency, before(day))
      const paid = round2(item.amount * between(0.3, 0.7))
      const truth = [row(item, paid, 'PARTIAL')]
      return { ...base, amount: sign * paid, concept: `PAGO A CUENTA ${item.printed}`, truth, proposal: truth, conf: between(0.5, 0.8), why: 'Número impreso, importe inferior', source: 'm1_reference', segment: 'ar_partial', difficulty: 2 }
    }
    case 'discount':
    case 'bank_charges':
    case 'withholding': {
      const { exception, share, terms, hint, segment, days } = SHORT[scenario]
      const item = nextItem('ar', pick(customers), between(400, 9000), currency, before(day, ...days), terms)
      const paid = round2(item.amount * (1 - share))
      const known = random() < 0.6
      const truth = [row(item, item.amount, exception)]
      const proposal = known ? truth : [row(item, paid)]
      return { ...base, payer: null, amount: paid, concept: `${item.printed} ${hint}`, truth, proposal, conf: known ? between(0.55, 0.85) : between(0.3, 0.5), why: known ? 'Diferencia compatible con la excepción' : 'Importe inferior sin causa clara', source: 'm3_tolerance', segment, difficulty: 2 }
    }
    case 'dispute': {
      const customer = pick(customers)
      const items = Array.from({ length: 2 }, () => nextItem('ar', customer, between(400, 4000), currency, before(day)))
      const paid = round2(items[0].amount * 0.9)
      const truth = [row(items[0], paid, 'SHORT_DISPUTE'), row(items[1], items[1].amount)]
      return { ...base, payer: null, amount: round2(paid + items[1].amount), concept: conceptWithName(customer), truth, proposal: truth, conf: between(0.6, 0.9), why: 'Aviso de remesa con deducción en una línea', source: 'm2_remittance', segment: 'ar_short_dispute', difficulty: 3 }
    }
    case 'overpayment': {
      const item = nextItem(kind, party, amountFor(), currency, before(day))
      const paid = round2(item.amount * between(1.02, 1.2))
      const truth = [row(item, item.amount, 'OVERPAYMENT')]
      return { ...base, amount: sign * paid, concept: `${item.printed} ${nameInText(party)}`, truth, proposal: truth, conf: between(0.55, 0.8), why: 'El movimiento supera la partida', source: 'm3_tolerance', segment: 'ar_overpayment', difficulty: 2 }
    }
    case 'fx': {
      const foreign = pick(['USD', 'GBP'])
      const item = nextItem(kind, party, between(200, 8000), foreign, before(day))
      const paid = round2(item.amount * EUR_PER_UNIT[foreign] * (1 - between(0.001, 0.006)))
      const truth = [row(item, item.amount, 'FX_DIFFERENCE')]
      return { ...base, currency: 'EUR', amount: sign * paid, origAmount: item.amount, origCurrency: foreign, concept: `${item.printed} DIVISA ${foreign}`, truth, proposal: truth, conf: between(0.45, 0.8), why: 'Número impreso, conversión de divisa', source: 'm3_fx', segment: 'ar_fx', difficulty: 2 }
    }
    case 'duplicate': {
      const item = nextItem(kind, party, amountFor(), currency, before(day))
      const truth = [row(item, item.amount, 'DUPLICATE_PAYMENT')]
      return { ...base, amount: sign * item.amount, concept: `DUPLICADO ${item.printed}`, truth, proposal: [row(item, item.amount)], conf: between(0.4, 0.7), why: 'Número impreso, partida ya cobrada', source: 'm1_reference', segment: 'duplicate_payment', difficulty: 3 }
    }
    case 'returned': {
      const item = nextItem('ar', pick(customers), amountFor(), currency, before(day))
      const truth = [row(item, item.amount, 'RETURNED')]
      return { ...base, payer: null, amount: -item.amount, concept: `DEVOLUCION RECIBO ${item.printed}`, truth, proposal: truth, conf: between(0.6, 0.9), why: 'El concepto indica devolución', source: 'm4_returns', segment: 'ar_dd_returned', difficulty: 2 }
    }
    case 'category': {
      const [code, , , concept] = pick(NO_ITEM_CATEGORIES)
      const amount = round2(between(8, 25000))
      const truth = [noItem(code)]
      const known = random() < 0.7
      return { ...base, payer: null, currency: 'EUR', amount: -amount, concept, truth, proposal: known ? truth : null, conf: between(0.7, 0.99), why: 'Clasificado por el texto', source: 'm5_nonitem', segment: `nonitem_${code.toLowerCase()}`, difficulty: 1 }
    }
    default: {
      const amount = round2(amountFor())
      const truth = [noItem('UNIDENTIFIED', 'UNIDENTIFIED')]
      return { ...base, payer: null, amount, concept: pick(['INGRESO EN EFECTIVO', 'TRANSFERENCIA SIN CONCEPTO', 'ABONO VARIOS']), truth, proposal: random() < 0.5 ? truth : null, conf: between(0.3, 0.7), why: 'Sin partida que encaje', source: 'm5_nonitem', segment: 'unidentified', difficulty: 3 }
    }
  }
}

const PROPOSAL_COVERAGE = 0.92

const BANK_YEAR_SEQ = 7

for (let i = 0; i < TXNS; i += 1) {
  const c = makeCase(i)
  const id = `BT-2024-${String(i + 1).padStart(BANK_YEAR_SEQ, '0')}`
  const date = dateOf(c.day)
  const [account, entity, bankCode] = accountFor(c.currency)
  const e2e = c.payer ? `E2E-${String(i + 1).padStart(8, '0')}` : ''
  bank.write([id, account, entity, bankCode, 'camt053', `${account}_${date.slice(0, 7)}.xml`, date, date, money(c.amount), c.currency, '', '', `${bankCode}${String(i + 1).padStart(10, '0')}`, '', c.concept, c.payer?.name ?? '', c.payer?.iban ?? '', e2e, '', c.origAmount ? money(c.origAmount) : '', c.origCurrency ?? ''])
  if (c.proposal && random() < PROPOSAL_COVERAGE) {
    for (const row of c.proposal) {
      proposals.write([id, row.item, row.item ? money(row.amount) : '', row.category, row.exception, c.source, c.conf.toFixed(3), c.why])
    }
  }
  for (const row of c.truth) {
    golden?.write([id, row.item, row.item ? money(row.amount) : '', row.category, row.exception, c.segment, c.difficulty])
  }
  if (i % 1000 === 0) {
    await Promise.all([ar, ap, cn, open, bank, proposals, golden].filter(Boolean).map((w) => w.flush()))
  }
}

// Paid-off and untouched items, to give the candidate search a realistic haystack.
while (itemSeq < ITEMS) {
  const incoming = random() < 0.7
  nextItem(incoming ? 'ar' : 'ap', incoming ? pick(customers) : pick(suppliers), amountFor(), pick(CURRENCIES), Math.floor(between(0, 300)))
  if (itemSeq % 5000 === 0) await Promise.all([ar, ap, cn, open].map((w) => w.flush()))
}

await Promise.all([ar, ap, cn, open, bank, proposals, golden].filter(Boolean).map((w) => w.close()))

const lines = (rows) => `${rows.map((r) => r.map(csv).join(',')).join('\n')}\n`
const partyRow = (party) => [party.id, 'ES00', party.name, '', party.iban, 'T30']
const MONTHS = Array.from({ length: 13 }, (_, i) => new Date(Date.UTC(2023, 11 + i, 1)).toISOString().slice(0, 10))
writeFileSync(join(OUT, 'masters/categories.csv'), lines([['category', 'description', 'gl_account'], ...CATEGORIES.map(([code, label, account]) => [code, label, account])]))
writeFileSync(join(OUT, 'masters/customers.csv'), lines([['customer_id', 'entity', 'name', 'trade_name', 'iban', 'payment_terms'], ...customers.map(partyRow)]))
writeFileSync(join(OUT, 'masters/suppliers.csv'), lines([['supplier_id', 'entity', 'name', 'trade_name', 'iban', 'payment_terms'], ...suppliers.map(partyRow)]))
writeFileSync(join(OUT, 'masters/payment_terms.csv'), lines([['code', 'days', 'early_payment_discount_pct', 'discount_days', 'description'], ['T30', 30, '', '', '30 días fecha factura'], ['D2N30', 30, '2.00', 10, '2 % 10 días, neto 30']]))
writeFileSync(
  join(OUT, 'masters/fx_rates.csv'),
  lines([['date', 'currency', 'units_per_eur', 'source'], ...MONTHS.flatMap((date) => ['USD', 'GBP'].map((code) => [date, code, (1 / EUR_PER_UNIT[code]).toFixed(4), 'ECB']))]),
)

/** Minimal single-page PDF so the text extraction can be exercised without assets. */
function tinyPdf(text) {
  const stream = `BT /F1 12 Tf 60 760 Td (${text.replace(/[()\\]/g, ' ')}) Tj ET`
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ]
  let body = '%PDF-1.4\n'
  const offsets = objects.map((object, i) => {
    const at = body.length
    body += `${i + 1} 0 obj\n${object}\nendobj\n`
    return at
  })
  const xref = body.length
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  body += offsets.map((o) => `${String(o).padStart(10, '0')} 00000 n \n`).join('')
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return body
}

const PDF_COUNT = 12
for (let i = 1; i <= PDF_COUNT; i += 1) {
  const printed = `FV24-${String(i).padStart(7, '0')}`
  writeFileSync(
    join(OUT, 'pdfs', `remesa_${String(i).padStart(3, '0')}.pdf`),
    tinyPdf(`Aviso de remesa. Se liquida la factura ${printed} con cargo a la cuenta del cliente.`),
  )
}

process.stdout.write(`${OUT}: ${TXNS} movimientos, ${itemSeq} partidas\n`)
