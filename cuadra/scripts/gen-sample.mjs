#!/usr/bin/env node
// Generates a synthetic dataset with the official file layout, so the app can be
// tried and load-tested without the real data. Never use it for a real submission.
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
const NAME_B = ['Logística', 'Textil', 'Alimentación', 'Energía', 'Montajes', 'Química', 'Transportes', 'Distribución', 'Ingeniería', 'Papelera', 'Envases', 'Maderas']
const FORM = ['SA', 'SL', 'SLU', 'SAU']
const CURRENCIES = ['EUR', 'EUR', 'EUR', 'EUR', 'USD', 'GBP']
const ACCOUNTS = ['ES76-0001-CAIXA', 'ES12-0049-SANTANDER', 'ES91-0182-BBVA', 'GB29-NWBK-LONDON']
const CATEGORIES = [
  ['BANK_FEES', 'Comisiones bancarias', 'COMISION MANTENIMIENTO CUENTA'],
  ['PAYROLL', 'Nóminas', 'NOMINAS REMESA MENSUAL'],
  ['TAXES', 'Impuestos', 'AEAT MODELO 303'],
  ['INTEREST', 'Intereses', 'LIQUIDACION INTERESES'],
  ['RENT', 'Alquileres', 'ALQUILER OFICINAS'],
  ['INTERNAL_TRANSFER', 'Traspaso interno', 'TRASPASO ENTRE CUENTAS'],
]
const RATES = { USD: 0.92, GBP: 1.17, EUR: 1 }

const partyCount = Math.max(40, Math.round(ITEMS / 60))
const uniqueName = (i) => `${pick(NAME_A)} ${pick(NAME_B)} ${i} ${pick(FORM)}`.toUpperCase()
const customers = Array.from({ length: partyCount }, (_, i) => ({ id: `C${String(i).padStart(5, '0')}`, name: uniqueName(i) }))
const suppliers = Array.from({ length: Math.round(partyCount / 2) }, (_, i) => ({ id: `S${String(i).padStart(5, '0')}`, name: uniqueName(i) }))

const dateOf = (dayOffset) => new Date(Date.UTC(2024, 0, 1 + dayOffset)).toISOString().slice(0, 10)

const ar = new Writer(join(OUT, 'ar_invoices.csv'), ['item_id', 'printed_number', 'customer_id', 'amount', 'currency', 'issue_date', 'due_date', 'payment_terms'])
const ap = new Writer(join(OUT, 'ap_invoices.csv'), ['item_id', 'printed_number', 'supplier_id', 'amount', 'currency', 'issue_date', 'due_date', 'payment_terms'])
const cn = new Writer(join(OUT, 'credit_notes.csv'), ['item_id', 'printed_number', 'customer_id', 'amount', 'currency', 'issue_date', 'due_date', 'payment_terms'])
const open = new Writer(join(OUT, 'open_items_at_start.csv'), ['item_id', 'open_amount'])
const bank = new Writer(join(OUT, 'bank_transactions.csv'), ['bank_txn_id', 'booking_date', 'amount', 'currency', 'account', 'description'])
const proposals = new Writer(join(OUT, 'propuestas.csv'), ['bank_txn_id', 'item_id', 'amount', 'exception', 'confidence', 'motivo'])
const golden = GOLDEN ? new Writer(join(OUT, 'golden.csv'), ['bank_txn_id', 'item_id', 'amount', 'category', 'exception']) : null

let itemSeq = 0
const nextItem = (kind, party, amount, currency, dayOffset, terms = 'NET30') => {
  itemSeq += 1
  const prefix = { ar: 'ARI', ap: 'API', cn: 'ARC' }[kind]
  const printPrefix = { ar: 'F', ap: 'P', cn: 'NC' }[kind]
  const item = {
    id: `${prefix}-${String(itemSeq).padStart(8, '0')}`,
    printed: `${printPrefix}-2024/${String(100000 + itemSeq)}`,
    party,
    amount: round2(amount),
    currency,
    due: dateOf(dayOffset + 30),
  }
  const writer = { ar, ap, cn }[kind]
  writer.write([item.id, item.printed, party.id, money(item.amount), currency, dateOf(dayOffset), item.due, terms])
  open.write([item.id, money(item.amount)])
  return item
}

const amountFor = () => (random() < 0.2 ? between(5000, 90000) : between(40, 4800))
const conceptWithName = (party) => `TRANSFERENCIA ${party.name}`
const conceptWithNumber = (item) => pick([`PAGO FRA ${item.printed} ${item.party.name}`, `REF ${item.printed}`, `ABONO ${item.printed.replace('-', ' ')}`.replace('/', '')])

const SCENARIOS = [
  ['printed', 34],
  ['exact_party', 16],
  ['fifo', 5],
  ['one_to_n', 8],
  ['credit_note', 3],
  ['partial', 4],
  ['discount', 3],
  ['bank_charges', 3],
  ['withholding', 2],
  ['dispute', 2],
  ['overpayment', 2],
  ['fx', 5],
  ['duplicate', 1],
  ['returned', 1],
  ['category', 7],
  ['unidentified', 4],
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

const accountFor = (currency) => (currency === 'GBP' ? ACCOUNTS[3] : pick(ACCOUNTS.slice(0, 3)))

/** Returns the bank movement, its true rows and what the pipeline would propose. */
function makeCase(index) {
  const scenario = pickScenario()
  const incoming = random() < 0.72
  const party = incoming ? pick(customers) : pick(suppliers)
  const kind = incoming ? 'ar' : 'ap'
  const sign = incoming ? 1 : -1
  const currency = pick(CURRENCIES)
  const day = Math.floor(between(0, 300))
  const row = (item, amount, exception = '') => ({ item: item?.id ?? '', amount, exception, category: '' })
  const noise = () => between(0.55, 0.99)

  switch (scenario) {
    case 'printed': {
      const item = nextItem(kind, party, amountFor(), currency, day)
      const truth = [row(item, item.amount)]
      return { amount: sign * item.amount, currency, day, concept: conceptWithNumber(item), truth, proposal: truth, conf: between(0.92, 0.999), why: 'Número impreso en el concepto e importe exacto' }
    }
    case 'exact_party': {
      const item = nextItem(kind, party, amountFor(), currency, day)
      const truth = [row(item, item.amount)]
      return { amount: sign * item.amount, currency, day, concept: conceptWithName(party), truth, proposal: truth, conf: between(0.75, 0.93), why: 'Mismo tercero e importe exacto' }
    }
    case 'fifo': {
      const amount = amountFor()
      const older = nextItem(kind, party, amount, currency, day - 40)
      const newer = nextItem(kind, party, amount, currency, day)
      const wrong = random() < 0.3
      return { amount: sign * amount, currency, day, concept: conceptWithName(party), truth: [row(older, older.amount)], proposal: [row(wrong ? newer : older, amount)], conf: between(0.55, 0.8), why: 'Varias partidas iguales del mismo tercero' }
    }
    case 'one_to_n': {
      const size = 2 + Math.floor(random() * 3)
      const items = Array.from({ length: size }, (_, i) => nextItem(kind, party, between(40, 3000), currency, day - i * 7))
      const total = items.reduce((sum, item) => sum + item.amount, 0)
      const truth = items.map((item) => row(item, item.amount))
      const complete = random() < 0.7
      return { amount: sign * round2(total), currency, day, concept: conceptWithName(party), truth, proposal: complete ? truth : truth.slice(0, 1), conf: complete ? between(0.6, 0.88) : between(0.3, 0.55), why: complete ? 'La combinación suma el importe' : 'Coincide el tercero, combinación incompleta' }
    }
    case 'credit_note': {
      const invoice = nextItem('ar', customers[index % customers.length], between(300, 4000), currency, day)
      const note = nextItem('cn', invoice.party, -round2(invoice.amount * between(0.05, 0.25)), currency, day)
      const net = round2(invoice.amount + note.amount)
      const truth = [row(invoice, invoice.amount), row(note, note.amount)]
      return { amount: net, currency, day, concept: conceptWithName(invoice.party), truth, proposal: truth, conf: between(0.6, 0.85), why: 'Factura neta de abono' }
    }
    case 'partial': {
      const item = nextItem(kind, party, amountFor(), currency, day)
      const paid = round2(item.amount * between(0.3, 0.7))
      const truth = [row(item, paid, 'PARTIAL')]
      return { amount: sign * paid, currency, day, concept: `PAGO A CUENTA ${item.printed}`, truth, proposal: truth, conf: between(0.5, 0.8), why: 'Número impreso, importe inferior' }
    }
    case 'discount':
    case 'bank_charges':
    case 'withholding':
    case 'dispute': {
      const exception = { discount: 'SHORT_DISCOUNT', bank_charges: 'SHORT_BANK_CHARGES', withholding: 'SHORT_WITHHOLDING', dispute: 'SHORT_DISPUTE' }[scenario]
      const cut = { discount: 0.02, bank_charges: 0.015, withholding: 0.15, dispute: 0.1 }[scenario]
      const terms = scenario === 'discount' ? '2-10-NET30' : 'NET30'
      const item = nextItem(kind, party, between(400, 9000), currency, day, terms)
      const paid = round2(item.amount * (1 - cut))
      const hint = { discount: 'PRONTO PAGO', bank_charges: 'GASTOS', withholding: 'RETENCION', dispute: 'RECLAMACION' }[scenario]
      const truth = [row(item, paid, exception)]
      const known = random() < 0.6
      return { amount: sign * paid, currency, day, concept: `${item.printed} ${hint}`, truth, proposal: [row(item, paid, known ? exception : '')], conf: known ? between(0.55, 0.85) : between(0.3, 0.5), why: known ? 'Diferencia compatible con la excepción' : 'Importe inferior sin causa clara' }
    }
    case 'overpayment': {
      const item = nextItem(kind, party, amountFor(), currency, day)
      const paid = round2(item.amount * between(1.02, 1.2))
      const truth = [row(item, item.amount, 'OVERPAYMENT')]
      return { amount: sign * paid, currency, day, concept: `${item.printed} ${party.name}`, truth, proposal: truth, conf: between(0.55, 0.8), why: 'El movimiento supera la partida' }
    }
    case 'fx': {
      const foreign = pick(['USD', 'GBP'])
      const item = nextItem(kind, party, between(200, 8000), foreign, day)
      const inEur = round2(item.amount * RATES[foreign])
      const exactRate = random() < 0.5
      const paid = exactRate ? inEur : round2(inEur * between(0.97, 1.03))
      const truth = [row(item, item.amount, exactRate ? '' : 'FX_DIFFERENCE')]
      return { amount: sign * paid, currency: 'EUR', day, concept: `${item.printed} DIVISA ${foreign}`, truth, proposal: truth, conf: between(0.45, 0.8), why: 'Número impreso, conversión de divisa' }
    }
    case 'duplicate': {
      const item = nextItem(kind, party, amountFor(), currency, day)
      const truth = [row(item, item.amount, 'DUPLICATE_PAYMENT')]
      return { amount: sign * item.amount, currency, day, concept: `DUPLICADO ${item.printed}`, truth, proposal: [row(item, item.amount)], conf: between(0.4, 0.7), why: 'Número impreso, partida ya cobrada' }
    }
    case 'returned': {
      const item = nextItem(kind, party, amountFor(), currency, day)
      const truth = [{ item: '', amount: item.amount, exception: 'RETURNED', category: '' }]
      return { amount: -sign * item.amount, currency, day, concept: `DEVOLUCION RECIBO ${item.printed}`, truth, proposal: truth, conf: between(0.6, 0.9), why: 'El concepto indica devolución' }
    }
    case 'category': {
      const [code, , concept] = pick(CATEGORIES)
      const amount = round2(between(8, 25000))
      return { amount: -amount, currency: 'EUR', day, concept, truth: [{ item: '', amount, exception: '', category: code }], proposal: null, conf: noise(), why: '' }
    }
    default: {
      const amount = round2(amountFor())
      return { amount: sign * amount, currency, day, concept: pick(['INGRESO EN EFECTIVO', 'TRANSFERENCIA SIN CONCEPTO', 'ABONO VARIOS', 'CARGO VARIOS']), truth: [{ item: '', amount, exception: 'UNIDENTIFIED', category: '' }], proposal: null, conf: noise(), why: '' }
    }
  }
}

const PROPOSAL_COVERAGE = 0.92

for (let i = 0; i < TXNS; i += 1) {
  const c = makeCase(i)
  const id = `TXN-${String(i + 1).padStart(8, '0')}`
  bank.write([id, dateOf(c.day), money(c.amount), c.currency, accountFor(c.currency), c.concept])
  if (c.proposal && random() < PROPOSAL_COVERAGE) {
    for (const row of c.proposal) {
      proposals.write([id, row.item, money(row.amount), row.exception, c.conf.toFixed(3), c.why])
    }
  }
  for (const row of c.truth) {
    golden?.write([id, row.item, money(row.amount), row.category, row.exception])
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
writeFileSync(join(OUT, 'masters/categories.csv'), lines([['category', 'description'], ...CATEGORIES.map(([code, label]) => [code, label])]))
writeFileSync(join(OUT, 'masters/customers.csv'), lines([['customer_id', 'name'], ...customers.map((c) => [c.id, c.name])]))
writeFileSync(join(OUT, 'masters/suppliers.csv'), lines([['supplier_id', 'name'], ...suppliers.map((s) => [s.id, s.name])]))
writeFileSync(join(OUT, 'masters/payment_terms.csv'), lines([['payment_terms', 'days', 'discount_pct'], ['NET30', 30, 0], ['2-10-NET30', 30, 2]]))
writeFileSync(
  join(OUT, 'masters/fx_rates.csv'),
  lines([['date', 'base_currency', 'quote_currency', 'rate'], ['2024-01-01', 'USD', 'EUR', RATES.USD], ['2024-01-01', 'GBP', 'EUR', RATES.GBP]]),
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
  const printed = `F-2024/${100000 + i}`
  writeFileSync(
    join(OUT, 'pdfs', `remesa_${String(i).padStart(3, '0')}.pdf`),
    tinyPdf(`Aviso de remesa. Se liquida la factura ${printed} con cargo a la cuenta del cliente.`),
  )
}

process.stdout.write(`${OUT}: ${TXNS} movimientos, ${itemSeq} partidas\n`)
