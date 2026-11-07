import { readdirSync, rmSync } from 'node:fs'
import { gzipSync } from 'node:zlib'
import { afterAll, describe, expect, test } from 'vitest'
import type { Db } from './db'
import { loadCase, searchItems } from './caseLoader'
import {
  bulkSummary,
  confirmBulk,
  importDecisions,
  loadDecisionsIntoDb,
  replayLog,
  undoBulk,
  writeDecision,
  type LogEntry,
} from './decisions'
import { buildExportRows, canDownloadSubmission, SUBMISSION_HEADER, validateExport, writeSubmission } from './exportSubmission'
import { ingest, type DatasetMeta } from './ingest'
import { buildPlan, fileSource, isGzip, readHeaders, type SourceFile } from './preflight'
import { buildQueue, DEFAULT_FILTERS, queuePage, viewCounts } from './queue'
import { loadDashboard, scoreAgainstGolden } from './stats'
import { buildDiagnosis, exportErrorsCsv, segmentReport } from './diagnosis'
import { createNodeDb } from './testDb'
import type { Decision } from '../domain/types'

const A1 = 'ARI-2024-0000001'
const A2 = 'ARI-2024-0000002'
const A3 = 'ARI-2024-0000003'
const P1 = 'API-2024-0000001'
const C1 = 'ARC-2024-0000001'

const BANK_HEADER = 'bank_txn_id,account_id,entity,bank,booking_date,value_date,amount,currency,text,counterparty_name,counterparty_account,end_to_end_id,remittance_info,orig_amount,orig_currency'

const FILES: Record<string, string> = {
  'bank_transactions.csv': `${BANK_HEADER}
T1,ES01,ES00,BBVA,2024-02-01,2024-02-01,100.00,EUR,PAGO FRA F-1001 ACME,,,,,,
T2,ES01,ES00,BBVA,2024-02-02,2024-02-02,150.00,EUR,TRANSFERENCIA ACME,,,,,,
T3,ES02,ES00,BBVA,2024-02-03,2024-02-03,-80.00,EUR,PAGO PROVEEDOR,,,,,,
T4,ES01,ES00,BBVA,2024-02-04,2024-02-04,-12.50,EUR,COMISION MANTENIMIENTO,,,,,,
T5,ES03,US01,CITI,2024-02-05,2024-02-05,999.00,USD,INGRESO DESCONOCIDO,,,,,,`,
  'ar_invoices.csv': `item_id,entity,document_number,issue_date,due_date,customer_id,customer_name,currency,total_amount,payment_terms,customer_po
${A1},ES00,F-1001,2024-01-01,2024-01-10,C1,,EUR,100.00,D2N30,PO-77
${A2},ES00,F-1002,2024-01-02,2024-01-20,C1,,EUR,100.00,T30,
${A3},ES00,F-1003,2024-01-03,2024-01-25,C1,,EUR,50.00,T30,`,
  'ap_invoices.csv': `item_id,entity,supplier_id,supplier_name,supplier_invoice_number,issue_date,due_date,currency,total_amount,payment_terms,our_po
${P1},ES00,S1,Globex SL,P-900,2024-01-04,2024-01-15,EUR,80.00,T30,`,
  'credit_notes.csv': `item_id,side,entity,document_number,issue_date,party_id,party_name,currency,total_amount,related_document,reason
${C1},AR,ES00,N-1,2024-01-05,C1,,EUR,10.00,F-1001,Abono`,
  'categories.csv': `category,description,gl_account
BANK_FEES,Comisiones bancarias,626
PAYROLL,Nóminas,640
UNIDENTIFIED,Sin identificar,555`,
  'customers.csv': `customer_id,entity,name,trade_name,iban,payment_terms
C1,ES00,ACME SA,,ES7700000000000000000001,T30`,
  'suppliers.csv': `supplier_id,entity,name,iban,payment_terms
S1,ES00,Globex SL,ES7700000000000000000002,T30`,
  'fx_rates.csv': `date,currency,units_per_eur,source
2024-01-01,USD,2.00,ECB
2024-02-01,USD,1.25,ECB`,
  'payment_terms.csv': `code,days,early_payment_discount_pct,discount_days,description
T30,30,,,30 días
D2N30,30,2.00,10,2 % 10 días`,
  'propuestas.csv': `bank_txn_id,item_id,amount,category,exception,confidence,motivo
T1,${A1},100.00,,,0.98,numero impreso
T2,${A2},100.00,,,0.70,mismo tercero
T2,${A3},50.00,,,0.70,mismo tercero
T3,${P1},80.00,,,0.60,importe exacto`,
  'golden.csv': `bank_txn_id,item_id,amount,category,exception,segment,difficulty
T1,${A1},100.00,,,ar_1to1,1
T2,${A2},100.00,,,ar_1toN,2
T2,${A3},50.00,,,ar_1toN,2
T3,${P1},80.00,,,ap_single,1
T4,,,BANK_FEES,,nonitem,1
T5,,,UNIDENTIFIED,UNIDENTIFIED,unidentified,3`,
}

const unidentified = (txnId: string, at = 1): Decision => ({
  txnId,
  allocations: [],
  category: 'UNIDENTIFIED',
  exception: 'UNIDENTIFIED',
  source: 'manual',
  at,
})

function sources(overrides: Record<string, string | Uint8Array | null> = {}): SourceFile[] {
  const merged = { ...FILES, ...overrides }
  return Object.entries(merged).flatMap(([name, content]) =>
    content === null ? [] : [fileSource(name, new File([content as BlobPart], name))],
  )
}

async function setup(overrides: Record<string, string | Uint8Array | null> = {}): Promise<{ db: Db; meta: DatasetMeta }> {
  const db = await createNodeDb()
  const plan = await buildPlan('demo', sources(overrides))
  const meta = await ingest(db, plan)
  return { db, meta }
}

async function csvLines(db: Db): Promise<string[]> {
  const bytes = await writeSubmission(db)
  return new TextDecoder().decode(bytes).trim().split('\n')
}

// The Node runtime of DuckDB writes COPY targets to the working directory.
afterAll(() => {
  for (const name of readdirSync('.')) {
    if (/^(tmp_)?out_.*\.csv$/.test(name)) rmSync(name)
  }
})

describe('engine output', () => {
  const OFFICIAL = 'bank_txn_id,item_id,amount,category,exception\nT1,,,BANK_FEES,\n'
  const EXPLAINED = 'bank_txn_id,item_id,amount,category,exception,rule,evidence\nT1,,,BANK_FEES,,kw:FEE,\n'
  const output = (path: string, text: string, lastModified = 0) =>
    fileSource(path, new File([gzipSync(text)], path.split('/').at(-1) ?? path, { lastModified }))

  test('prefers what the engine explains, even over a newer file', async () => {
    const plan = await buildPlan('demo', [
      ...sources({ 'propuestas.csv': null }),
      output('out/sub_live.csv.gz', OFFICIAL, 5),
      output('out/live/submission_m2.csv.gz', OFFICIAL, 9),
      output('out/explain_live.csv.gz', EXPLAINED, 1),
    ])
    expect(plan.files.proposals?.path).toBe('out/explain_live.csv.gz')
    expect(plan.files.proposals?.mapping).toMatchObject({ source: 'rule', reason: 'evidence' })
  })

  test('does not mix old stages into a file that already says where each row came from', async () => {
    const plan = await buildPlan('demo', [
      ...sources({ 'propuestas.csv': null }),
      output('out/explain_live.csv.gz', EXPLAINED),
      output('out/live/matches/m2_remittance.csv.gz', OFFICIAL),
    ])
    expect(plan.enrichments).toEqual([])
    expect(plan.proposalOptions).toHaveLength(2)
  })

  test('keeps the stages for a pipeline file that names its source but brings no confidence', async () => {
    const named = 'bank_txn_id,item_id,amount,category,exception,source\nT1,,,BANK_FEES,,m1_reference\n'
    const plan = await buildPlan('demo', [
      ...sources({ 'propuestas.csv': null }),
      output('out/live/propuestas.csv.gz', named),
      output('out/live/matches/m2_remittance.csv.gz', OFFICIAL),
    ])
    expect(plan.enrichments.map((f) => f.path)).toEqual(['out/live/matches/m2_remittance.csv.gz'])
  })

  test('keeps the rule and the evidence of each row, and knows there is no confidence', async () => {
    const explain = `bank_txn_id,item_id,amount,category,exception,rule,evidence\nT1,${A1},100.00,,,ref,refs F-1`
    const { db, meta } = await setup({ 'propuestas.csv': null, 'out/explain_live.csv': explain })
    const data = await loadCase(db, meta, 1)
    expect(meta.hasConfidence).toBe(false)
    expect(data.proposal[0]).toMatchObject({ source: 'ref', reason: 'refs F-1', confidence: null })
  })

  test('knows when the proposals carry confidence', async () => {
    const { meta } = await setup()
    expect(meta.hasConfidence).toBe(true)
  })
})

describe('preflight', () => {
  const gz = (text: string) => new File([gzipSync(text)], 'x.csv.gz')

  test('recognises gzip by its first bytes, whatever the name says', async () => {
    expect(await isGzip(gz('a,b'))).toBe(true)
    expect(await isGzip(new File(['a,b'], 'x.csv.gz'))).toBe(false)
  })

  test('reads the header of a compressed file', async () => {
    expect(await readHeaders(gz('bank_txn_id,amount\nT1,1\n'))).toEqual(['bank_txn_id', 'amount'])
  })

  test('picks the merged file over the baseline and lists the options', async () => {
    const header = 'bank_txn_id,item_id,amount,category,exception\nT1,,,BANK_FEES,\n'
    const plan = await buildPlan('demo', [
      ...sources({ 'propuestas.csv': null }),
      fileSource('out/live/baseline.csv.gz', new File([gzipSync(header)], 'baseline.csv.gz')),
      fileSource('out/live/submission_m2.csv.gz', new File([gzipSync(header)], 'submission_m2.csv.gz')),
      fileSource('out/live/matches/m2_remittance.csv.gz', new File([gzipSync(header)], 'm2_remittance.csv.gz')),
    ])
    expect(plan.files.proposals?.path).toBe('out/live/submission_m2.csv.gz')
    expect(plan.proposalOptions).toHaveLength(3)
    expect(plan.enrichments.map((f) => f.path)).toEqual(['out/live/matches/m2_remittance.csv.gz'])
  })

  test('honours the proposal file the reviewer picks', async () => {
    const header = 'bank_txn_id,item_id,amount,category,exception\nT1,,,BANK_FEES,\n'
    const plan = await buildPlan(
      'demo',
      [...sources({ 'propuestas.csv': null }), fileSource('out/live/baseline.csv', new File([header], 'baseline.csv')), fileSource('out/live/submission_a.csv', new File([header], 'submission_a.csv'))],
      { proposalPath: 'out/live/baseline.csv' },
    )
    expect(plan.files.proposals?.path).toBe('out/live/baseline.csv')
  })

  test('does not open the PDFs while planning', async () => {
    let opened = 0
    const pdf: SourceFile = {
      path: 'pdfs/ES01/a.pdf',
      open: async () => {
        opened += 1
        return new File([], 'a.pdf')
      },
    }
    const plan = await buildPlan('demo', [...sources(), pdf])
    expect(plan.pdfs).toHaveLength(1)
    expect(opened).toBe(0)
  })
})

describe('ingest', () => {
  test('reads the official files and resolves aliases', async () => {
    const { meta } = await setup()
    expect(meta.txnCount).toBe(5)
    expect(meta.itemCount).toBe(5)
    expect(meta.proposalTxnCount).toBe(3)
    expect(meta.categories).toEqual(['BANK_FEES', 'PAYROLL', 'UNIDENTIFIED'])
    expect(meta.categoryLabels.get('BANK_FEES')).toBe('Comisiones bancarias')
    expect(meta.hasGolden).toBe(true)
  })

  test('reads compressed files like plain ones', async () => {
    const { meta } = await setup({
      'bank_transactions.csv': null,
      'bank_transactions.csv.gz': gzipSync(FILES['bank_transactions.csv']),
      'ar_invoices.csv': null,
      'ar_invoices.csv.gz': gzipSync(FILES['ar_invoices.csv']),
    })
    expect(meta.txnCount).toBe(5)
    expect(meta.itemCount).toBe(5)
  })

  test('uses the bank text as the concept and keeps the other bank columns', async () => {
    const { db, meta } = await setup({
      'bank_transactions.csv': `${BANK_HEADER}
T1,ES01,ES00,BBVA,2024-02-01,2024-02-01,100.00,EUR,TEXTO DEL BANCO,ACME SA,ES7700000000000000000001,E2E-1,REMESA 1,120.00,USD`,
    })
    const { txn } = await loadCase(db, meta, 1)
    expect(txn).toMatchObject({
      description: 'TEXTO DEL BANCO',
      remittance: 'REMESA 1',
      counterparty: 'ACME SA',
      counterpartyAccount: 'ES7700000000000000000001',
      endToEnd: 'E2E-1',
      entity: 'ES00',
      bank: 'BBVA',
      origAmount: 120,
      origCurrency: 'USD',
    })
  })

  test('adds the items open at the start as candidates of their own', async () => {
    const { db, meta } = await setup({
      'open_items_at_start.csv': `item_id,item_type,entity,party_id,document_number,issue_date,due_date,currency,total_amount,open_amount
ARI-2023-0000009,ARI,ES00,C1,F-0009,2023-06-01,2023-07-01,EUR,500.00,100.00`,
    })
    expect(meta.itemCount).toBe(6)
    expect(meta.openItemCount).toBe(1)
    const data = await loadCase(db, meta, 1)
    const old = data.items.get('ARI-2023-0000009')
    expect(old).toMatchObject({ partyName: 'ACME SA', openAmount: 100, amount: 500, printed: 'F-0009' })
  })

  test('an open balance overrides the amount of an item the phase also lists', async () => {
    const { db, meta } = await setup({ 'open_items_at_start.csv': `item_id,open_amount\n${A1},60.00` })
    const [row] = await db.query(`SELECT open_amount FROM items WHERE item_id = '${A1}'`)
    expect(row.open_amount).toBe(60)
    expect(meta.itemCount).toBe(5)
  })

  test('reads rates given as units per euro', async () => {
    const { meta } = await setup()
    expect(meta.rates).toContainEqual({ date: '2024-02-01', base: 'EUR', quote: 'USD', rate: 1.25 })
  })

  test('reads the discount of the payment terms and its days', async () => {
    const { meta } = await setup()
    expect(meta.terms.get('D2N30')).toEqual({ pct: 2, days: 10 })
    expect(meta.terms.has('T30')).toBe(false)
  })

  test('warns about proposals for movements that do not exist', async () => {
    const { meta } = await setup({ 'propuestas.csv': `bank_txn_id,item_id,amount,exception,confidence,motivo\nT99,${A1},1,,0.5,x` })
    expect(meta.warnings.join(' ')).toContain('no existen')
  })

  test('parses amounts with a decimal comma', async () => {
    const { db } = await setup({
      'bank_transactions.csv': 'bank_txn_id,date,amount,currency,account,description\nT1,2024-01-01,"1.234,56",EUR,A,X',
    })
    const [row] = await db.query('SELECT amount FROM txn')
    expect(row.amount).toBeCloseTo(1234.56)
  })

  test('scales a confidence given as a percentage', async () => {
    const { db } = await setup({
      'propuestas.csv': `bank_txn_id,item_id,amount,exception,confidence,motivo\nT1,${A1},100,,95,x`,
    })
    const [row] = await db.query('SELECT conf FROM prop')
    expect(row.conf).toBeCloseTo(0.95)
  })

  test('takes source and confidence from the matching stages when the merged file lacks them', async () => {
    const stage = `bank_txn_id,item_id,amount,category,exception,source,confidence,advice_id,line_no
T1,${A1},100.00,,,m2_remittance,0.8,RA-7,3`
    const merged = `bank_txn_id,item_id,amount,category,exception\nT1,${A1},100.00,,\nT2,${A2},100.00,,`
    const db = await createNodeDb()
    const plan = await buildPlan('demo', [
      ...sources({ 'propuestas.csv': null }),
      fileSource('out/x/submission_m2.csv', new File([merged], 'submission_m2.csv')),
      fileSource('out/x/matches/m2_remittance.csv', new File([stage], 'm2_remittance.csv')),
    ])
    const meta = await ingest(db, plan)
    const first = await loadCase(db, meta, 1)
    expect(first.proposal[0]).toMatchObject({ confidence: 0.8, source: 'm2_remittance', adviceId: 'RA-7', lineNo: '3' })
    const second = await loadCase(db, meta, 2)
    expect(second.proposal[0]).toMatchObject({ confidence: null, source: '' })
  })
})

describe('queue', () => {
  const byAmount = { ...DEFAULT_FILTERS, view: 'all' as const, sort: 'amount' as const }

  test('orders by amount in euros, so currencies can be compared', async () => {
    const { db } = await setup({
      'bank_transactions.csv': `${BANK_HEADER}
T1,ES01,ES00,BBVA,2024-02-05,2024-02-05,500.00,EUR,EN EUROS,,,,,,
T2,ES03,US01,CITI,2024-02-05,2024-02-05,550.00,USD,EN DOLARES,,,,,,`,
      'propuestas.csv': null,
    })
    await buildQueue(db, byAmount)
    expect((await queuePage(db, 0, 10)).map((r) => r.id)).toEqual(['T1', 'T2'])
  })

  test('the priority view holds what is pending and not ready to confirm', async () => {
    const { db } = await setup()
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, view: 'priority' })).toBe(4)
    await writeDecision(db, 5, unidentified('T5'))
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, view: 'priority' })).toBe(3)
  })

  test('the priority view puts first what can raise the score most', async () => {
    const { db } = await setup()
    await buildQueue(db, { ...DEFAULT_FILTERS, view: 'priority' })
    // Nothing proposed counts as total doubt; then the less sure proposal goes first.
    expect((await queuePage(db, 0, 10)).map((r) => r.id)).toEqual(['T5', 'T4', 'T3', 'T2'])
  })

  test('a line with many pairs weighs more than one with few at the same confidence', async () => {
    const { db } = await setup({
      'propuestas.csv': `bank_txn_id,item_id,amount,category,exception,confidence,motivo
T1,${A1},100.00,,,0.5,x
T2,${A2},100.00,,,0.5,x
T2,${A3},50.00,,,0.5,x`,
    })
    await buildQueue(db, { ...DEFAULT_FILTERS, view: 'priority' })
    const ids = (await queuePage(db, 0, 10)).map((r) => r.id)
    expect(ids.indexOf('T2')).toBeLessThan(ids.indexOf('T1'))
  })

  test('the ready view holds what is above the threshold', async () => {
    const { db } = await setup()
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, view: 'ready' })).toBe(1)
    expect((await queuePage(db, 0, 10))[0].id).toBe('T1')
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, view: 'ready', readyThreshold: 0.65 })).toBe(2)
  })

  test('the exceptions and no proposal views', async () => {
    const { db } = await setup({
      'propuestas.csv': `bank_txn_id,item_id,amount,category,exception,confidence,motivo
T1,${A1},100.00,,PARTIAL,0.9,x`,
    })
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, view: 'exceptions' })).toBe(1)
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, view: 'no_proposal' })).toBe(4)
  })

  test('the done view holds the decided movements, to correct them', async () => {
    const { db } = await setup()
    await writeDecision(db, 5, unidentified('T5'))
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, view: 'done' })).toBe(1)
    expect((await queuePage(db, 0, 10))[0].resolved).toBe(true)
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, view: 'all' })).toBe(5)
  })

  test('counts every view at once and applies the filters', async () => {
    const { db } = await setup()
    await writeDecision(db, 5, unidentified('T5'))
    expect(await viewCounts(db, DEFAULT_FILTERS)).toMatchObject({
      priority: 3,
      ready: 1,
      exceptions: 0,
      no_proposal: 1,
      done: 1,
      all: 5,
      totalAll: 5,
      doneAll: 1,
    })
    const filtered = await viewCounts(db, { ...DEFAULT_FILTERS, currency: 'USD' })
    expect(filtered).toMatchObject({ all: 1, done: 1, priority: 0, totalAll: 5, doneAll: 1 })
  })

  test('filters by case type, party and entity', async () => {
    const { db } = await setup()
    expect(await buildQueue(db, { ...byAmount, caseType: 'uno_a_n' })).toBe(1)
    expect(await buildQueue(db, { ...byAmount, party: 'globex' })).toBe(1)
    expect(await buildQueue(db, { ...byAmount, entity: 'US01' })).toBe(1)
  })

  test('finds a movement by its id', async () => {
    const { db } = await setup()
    expect(await buildQueue(db, { ...byAmount, party: 't3' })).toBe(1)
  })

  test('sorts lowest confidence first when asked', async () => {
    const { db } = await setup()
    await buildQueue(db, { ...byAmount, sort: 'confidence_low', caseType: 'uno_a_uno' })
    expect((await queuePage(db, 0, 10))[0].id).toBe('T3')
  })
})

describe('loadCase', () => {
  test('finds the printed number, the exact amount and the party', async () => {
    const { db, meta } = await setup()
    const data = await loadCase(db, meta, 1)
    expect(data.raw.printed.map((p) => p.item.itemId)).toEqual([A1])
    expect(data.raw.printed[0].fragment).toBe('F-1001')
    expect(data.raw.exact.map((i) => i.itemId)).toEqual([A1, A2])
    expect(data.raw.party.map((i) => i.itemId)).toContain(A3)
  })

  test('finds a printed number split by the bank', async () => {
    const { db, meta } = await setup({
      'bank_transactions.csv': 'bank_txn_id,date,amount,currency,account,description\nT1,2024-02-01,100,EUR,A,ABONO F 1001',
    })
    const data = await loadCase(db, meta, 1)
    expect(data.raw.printed.map((p) => p.item.itemId)).toEqual([A1])
    expect(data.matchedFragments).toEqual(['1001'])
  })

  test('finds the party by the name in the concept', async () => {
    const { db, meta } = await setup({ 'bank_transactions.csv': 'bank_txn_id,date,amount,currency,account,description\nT1,2024-02-01,1,EUR,A,ABONO ACME SA' })
    const data = await loadCase(db, meta, 1)
    expect(data.raw.party.length).toBeGreaterThan(0)
  })

  test('finds the party by a name the bank cut short', async () => {
    const { db, meta } = await setup({
      'customers.csv': 'customer_id,entity,name,iban\nC1,ES00,Prieto y Llorente Suministros S.L.,ES77',
      'bank_transactions.csv': 'bank_txn_id,date,amount,currency,account,description\nT1,2024-02-01,1,EUR,A,TRANSFERENCIAS PRIETO Y LLORENTE SUMIN',
    })
    expect((await loadCase(db, meta, 1)).raw.party.length).toBeGreaterThan(0)
  })

  test('finds the party by the account that paid', async () => {
    const { db, meta } = await setup({
      'bank_transactions.csv': `${BANK_HEADER}
T1,ES01,ES00,BBVA,2024-02-01,2024-02-01,1.00,EUR,SEPA EREF,,ES7700000000000000000001,,,,`,
    })
    expect((await loadCase(db, meta, 1)).raw.party.length).toBeGreaterThan(0)
  })

  test('only offers items of the right side for the exact amount', async () => {
    const { db, meta } = await setup()
    const data = await loadCase(db, meta, 3)
    expect(data.raw.exact.map((i) => i.itemId)).toEqual([P1])
  })

  test('does not offer an invoice issued after the movement', async () => {
    const { db, meta } = await setup({
      'ar_invoices.csv': `item_id,entity,document_number,issue_date,due_date,customer_id,customer_name,currency,total_amount,payment_terms,customer_po
${A1},ES00,F-1001,2024-01-01,2024-01-10,C1,,EUR,100.00,T30,
${A2},ES00,F-1002,2024-06-01,2024-07-01,C1,,EUR,100.00,T30,`,
    })
    const data = await loadCase(db, meta, 1)
    expect(data.raw.exact.map((i) => i.itemId)).toEqual([A1])
    expect(data.raw.party.map((i) => i.itemId)).not.toContain(A2)
  })

  test('keeps the exception of each proposed row', async () => {
    const { db, meta } = await setup({
      'propuestas.csv': `bank_txn_id,item_id,amount,category,exception,confidence,motivo
T2,${A2},100.00,,SHORT_DISPUTE,0.7,x
T2,${A3},50.00,,,0.7,x`,
    })
    const data = await loadCase(db, meta, 2)
    expect(data.proposal.map((p) => p.exception)).toEqual(['SHORT_DISPUTE', null])
  })

  test('a saved decision comes back with its exception per row', async () => {
    const { db, meta } = await setup()
    await writeDecision(db, 2, {
      txnId: 'T2',
      allocations: [
        { itemId: A2, amount: 90, exception: 'SHORT_DISPUTE' },
        { itemId: A3, amount: 50, exception: null },
      ],
      category: null,
      exception: null,
      source: 'manual',
      at: 1,
    })
    const data = await loadCase(db, meta, 2)
    expect(data.decision?.allocations).toEqual([
      { itemId: A2, amount: 90, exception: 'SHORT_DISPUTE' },
      { itemId: A3, amount: 50, exception: null },
    ])
  })

  test('searches an amount however it is typed', async () => {
    const { db } = await setup()
    for (const typed of ['80.00', '80,00', '80']) {
      expect((await searchItems(db, typed)).map((i) => i.itemId)).toEqual([P1])
    }
  })

  test('marks the whole number when the bank split it in two', async () => {
    const { db, meta } = await setup({
      'ar_invoices.csv': `item_id,entity,document_number,issue_date,due_date,customer_id,customer_name,currency,total_amount
${A1},ES00,FV24-0001001,2024-01-01,2024-01-10,C1,,EUR,100.00`,
      'bank_transactions.csv': 'bank_txn_id,date,amount,currency,account,description\nT1,2024-02-01,100,EUR,A,ABONO FV24 0001001',
      'propuestas.csv': null,
    })
    const data = await loadCase(db, meta, 1)
    expect(data.matchedFragments).toEqual(['FV24', '0001001'])
  })

  test('searches items by the purchase order too', async () => {
    const { db } = await setup()
    expect((await searchItems(db, 'PO-77')).map((i) => i.itemId)).toEqual([A1])
  })
})

describe('export', () => {
  test('offers a submission file only for phases that cannot be uploaded', () => {
    expect(canDownloadSubmission('train')).toBe(true)
    expect(canDownloadSubmission('sample')).toBe(true)
    expect(canDownloadSubmission('live')).toBe(false)
    expect(canDownloadSubmission('final')).toBe(false)
    expect(canDownloadSubmission(null)).toBe(false)
  })

  const options = { includeProposals: true, minConfidence: 0 }

  test('writes the exact header and marks the rest as UNIDENTIFIED, as the format asks', async () => {
    const { db } = await setup()
    const summary = await buildExportRows(db, options)
    const lines = await csvLines(db)
    expect(lines[0]).toBe(SUBMISSION_HEADER)
    expect(summary.txns).toBe(5)
    expect(summary.autoUnidentified).toBe(2)
    expect(lines).toContain('T5,,,UNIDENTIFIED,UNIDENTIFIED')
    expect(lines).toContain(`T1,${A1},100.00,,`)
  })

  test('leaves the amount empty on a line without items', async () => {
    const { db } = await setup()
    await writeDecision(db, 4, { txnId: 'T4', allocations: [], category: 'BANK_FEES', exception: null, source: 'manual', at: 1 })
    await buildExportRows(db, options)
    expect(await csvLines(db)).toContain('T4,,,BANK_FEES,')
  })

  test('always writes positive amounts', async () => {
    const { db } = await setup({ 'propuestas.csv': `bank_txn_id,item_id,amount,category,exception,confidence,motivo\nT3,${P1},-80.00,,,0.9,x` })
    await buildExportRows(db, options)
    expect(await csvLines(db)).toContain(`T3,${P1},80.00,,`)
  })

  test('writes the exception of each row', async () => {
    const { db } = await setup()
    await writeDecision(db, 2, {
      txnId: 'T2',
      allocations: [
        { itemId: A2, amount: 100, exception: 'SHORT_DISPUTE' },
        { itemId: A3, amount: 50, exception: null },
      ],
      category: null,
      exception: null,
      source: 'manual',
      at: 1,
    })
    await buildExportRows(db, options)
    const lines = await csvLines(db)
    expect(lines).toContain(`T2,${A2},100.00,,SHORT_DISPUTE`)
    expect(lines).toContain(`T2,${A3},50.00,,`)
  })

  test('a reviewed decision beats the proposal', async () => {
    const { db } = await setup()
    await writeDecision(db, 1, {
      txnId: 'T1',
      allocations: [{ itemId: A2, amount: 100, exception: 'SHORT_DISCOUNT' }],
      category: null,
      exception: null,
      source: 'manual',
      at: 1,
    })
    await buildExportRows(db, options)
    const lines = await csvLines(db)
    expect(lines).toContain(`T1,${A2},100.00,,SHORT_DISCOUNT`)
    expect(lines.filter((l) => l.startsWith('T1,'))).toHaveLength(1)
  })

  test('leaves proposals below the threshold as UNIDENTIFIED', async () => {
    const { db } = await setup()
    const summary = await buildExportRows(db, { includeProposals: true, minConfidence: 0.9 })
    expect(summary.fromProposals).toBe(1)
    expect(summary.autoUnidentified).toBe(4)
  })

  test('validates a clean export without errors', async () => {
    const { db, meta } = await setup()
    await buildExportRows(db, options)
    expect((await validateExport(db, meta.categories)).filter((i) => i.severity === 'error')).toEqual([])
  })

  const issueCodes = async (propuestas: string) => {
    const { db, meta } = await setup({ 'propuestas.csv': `bank_txn_id,item_id,amount,category,exception,confidence,motivo\n${propuestas}` })
    await buildExportRows(db, options)
    return validateExport(db, meta.categories)
  }

  test('warns about an item of another phase without blocking, because the server accepts it', async () => {
    const issue = (await issueCodes('T1,ARI-2024-0000404,100,,RETURNED,0.9,x')).find((i) => i.code === 'unknown_item')
    expect(issue?.severity).toBe('warning')
    expect(issue?.count).toBe(1)
    expect(issue?.samples[0]).toMatchObject({ line: 2, txn: 'T1', detail: 'ARI-2024-0000404' })
  })

  test('reports an item id the server would reject', async () => {
    const codes = (await issueCodes('T1,FACTURA-1,100,,,0.9,x')).map((i) => i.code)
    expect(codes).toContain('bad_item_id')
  })

  test('reports duplicate pairs', async () => {
    const issue = (await issueCodes(`T1,${A1},50,,,0.9,x\nT1,${A1},50,,,0.9,x`)).find((i) => i.code === 'duplicate_pair')
    expect(issue?.count).toBe(2)
  })

  test('reports a line with items and a category at once', async () => {
    const codes = (await issueCodes(`T1,${A1},100,,,0.9,x\nT1,,,BANK_FEES,,0.9,x`)).map((i) => i.code)
    expect(codes).toContain('items_and_category')
  })

  test('reports two rows without item on one line', async () => {
    const codes = (await issueCodes('T1,,,BANK_FEES,,0.9,x\nT1,,,PAYROLL,,0.9,x')).map((i) => i.code)
    expect(codes).toContain('duplicate_no_item')
  })

  test('reports an item row without amount', async () => {
    const issues = await issueCodes(`T1,ARI-2024-0000404,,,,0.9,x`)
    expect(issues.find((i) => i.code === 'bad_amount')?.severity).toBe('error')
  })

  test('reports exceptions outside the closed list', async () => {
    expect((await issueCodes(`T1,${A1},100,,NOPE,0.9,x`)).map((i) => i.code)).toContain('unknown_exception')
  })

  test('reports a category outside categories.csv', async () => {
    const { db, meta } = await setup()
    await writeDecision(db, 4, { txnId: 'T4', allocations: [], category: 'INVENTADA', exception: null, source: 'manual', at: 1 })
    await buildExportRows(db, options)
    expect((await validateExport(db, meta.categories)).map((i) => i.code)).toContain('unknown_category')
  })

  test('warns when an allocation does not add up without an exception', async () => {
    const { db, meta } = await setup()
    await writeDecision(db, 1, {
      txnId: 'T1',
      allocations: [{ itemId: A1, amount: 60, exception: null }],
      category: null,
      exception: null,
      source: 'manual',
      at: 1,
    })
    await buildExportRows(db, options)
    expect((await validateExport(db, meta.categories)).find((i) => i.code === 'unbalanced')?.severity).toBe('warning')
  })

  test('does not warn about a line with an exception on any of its rows', async () => {
    const { db, meta } = await setup()
    await writeDecision(db, 2, {
      txnId: 'T2',
      allocations: [
        { itemId: A2, amount: 100, exception: 'SHORT_DISPUTE' },
        { itemId: A3, amount: 5, exception: null },
      ],
      category: null,
      exception: null,
      source: 'manual',
      at: 1,
    })
    await buildExportRows(db, options)
    expect((await validateExport(db, meta.categories)).map((i) => i.code)).not.toContain('unbalanced')
  })

  test('reports the size against the limits of the server', async () => {
    const { db } = await setup()
    const summary = await buildExportRows(db, options)
    expect(summary.bytes).toBeGreaterThan(100)
    expect(summary.bytes).toBeLessThan(1000)
  })
})

describe('decisions', () => {
  test('summarises a batch before confirming it', async () => {
    const { db } = await setup({
      'propuestas.csv': `bank_txn_id,item_id,amount,category,exception,confidence,motivo
T1,${A1},100.00,,PARTIAL,0.99,x
T2,${A2},100.00,,,0.99,x
T2,${A3},50.00,,,0.99,x
T3,${P1},80.00,,,0.2,x`,
    })
    await buildQueue(db, { ...DEFAULT_FILTERS, view: 'all' })
    expect(await bulkSummary(db, 0.9)).toEqual({ lines: 2, rows: 3, withException: 1 })
  })

  test('bulk confirms only at or above the threshold and can be undone', async () => {
    const { db } = await setup()
    await buildQueue(db, { ...DEFAULT_FILTERS, view: 'all' })
    const result = await confirmBulk(db, 0.9)
    expect(result.count).toBe(1)
    const [after] = await db.query('SELECT count(DISTINCT rid) AS n FROM decision_rows')
    expect(after.n).toBe(1)
    await undoBulk(db, result.batch)
    const [undone] = await db.query('SELECT count(*) AS n FROM decision_rows')
    expect(undone.n).toBe(0)
  })

  test('bulk keeps the exception of each proposed row', async () => {
    const { db } = await setup({
      'propuestas.csv': `bank_txn_id,item_id,amount,category,exception,confidence,motivo
T2,${A2},100.00,,SHORT_DISPUTE,0.99,x
T2,${A3},50.00,,,0.99,x`,
    })
    await buildQueue(db, { ...DEFAULT_FILTERS, view: 'all' })
    const bulk = await confirmBulk(db, 0.9)
    const [decision] = replayLog([{ kind: 'bulk', batch: bulk.batch, csv: bulk.csv, source: 'bulk', at: 1 }])
    expect(decision.allocations.map((a) => a.exception).sort()).toEqual(['SHORT_DISPUTE', null].sort())
  })

  test('replaying the log rebuilds the same decisions', async () => {
    const { db } = await setup()
    await buildQueue(db, { ...DEFAULT_FILTERS, view: 'all' })
    const bulk = await confirmBulk(db, 0.65)
    const entries: LogEntry[] = [
      { kind: 'bulk', batch: bulk.batch, csv: bulk.csv, source: 'bulk', at: 1 },
      { kind: 'set', decision: unidentified('T5', 2) },
      { kind: 'clear', txnId: 'T1' },
    ]
    const decisions = replayLog(entries)
    expect(decisions.map((d) => d.txnId).sort()).toEqual(['T2', 'T5'])
    expect(decisions.find((d) => d.txnId === 'T2')?.allocations).toHaveLength(2)

    const fresh = await setup()
    await loadDecisionsIntoDb(fresh.db, decisions)
    const [row] = await fresh.db.query(`SELECT category, exception FROM decision_rows WHERE txn_id = 'T5'`)
    expect(row).toMatchObject({ category: 'UNIDENTIFIED', exception: 'UNIDENTIFIED' })
  })

  test('replay moves the line exception of an old log onto its rows', () => {
    const old = { txnId: 'T1', allocations: [{ itemId: A1, amount: 60 }], category: null, exception: 'PARTIAL', source: 'manual', at: 1 }
    const [decision] = replayLog([{ kind: 'set', decision: old as unknown as Decision }])
    expect(decision.allocations).toEqual([{ itemId: A1, amount: 60, exception: 'PARTIAL' }])
    expect(decision.exception).toBeNull()
  })

  test('replay drops a bulk batch that was undone', () => {
    const entries: LogEntry[] = [
      { kind: 'bulk', batch: 'b1', csv: 'bank_txn_id,item_id,amount,category,exception\nT1,A,1,,', source: 'bulk', at: 1 },
      { kind: 'unbulk', batch: 'b1' },
    ]
    expect(replayLog(entries)).toEqual([])
  })

  test('import normalises codes the way the server does', async () => {
    const { db, meta } = await setup()
    await importDecisions(db, `bank_txn_id,item_id,amount,category,exception\nT1,${A1},100,,partial\nT4,,,bank_fees,`)
    const rows = await db.query(`SELECT txn_id, category, exception FROM decision_rows ORDER BY txn_id`)
    expect(rows).toEqual([
      expect.objectContaining({ txn_id: 'T1', exception: 'PARTIAL' }),
      expect.objectContaining({ txn_id: 'T4', category: 'BANK_FEES' }),
    ])
    await buildExportRows(db, { includeProposals: true, minConfidence: 0 })
    expect((await validateExport(db, meta.categories)).filter((i) => i.severity === 'error')).toEqual([])
  })

  test('replay reads lowercase codes too', () => {
    const [decision] = replayLog([{ kind: 'bulk', batch: 'b', csv: `bank_txn_id,item_id,amount,category,exception\nT1,${A1},1,,partial`, source: 'imported', at: 1 }])
    expect(decision.allocations[0].exception).toBe('PARTIAL')
  })

  test('a row without amount stays without it after a reload, so the export still flags it', async () => {
    const decisions = replayLog([{ kind: 'bulk', batch: 'b', csv: `bank_txn_id,item_id,amount,category,exception\nT1,${A1},,,`, source: 'imported', at: 1 }])
    const { db, meta } = await setup()
    await loadDecisionsIntoDb(db, decisions)
    const [row] = await db.query(`SELECT amount FROM decision_rows WHERE txn_id = 'T1'`)
    expect(row.amount).toBeNull()
    await buildExportRows(db, { includeProposals: true, minConfidence: 0 })
    expect((await validateExport(db, meta.categories)).map((i) => i.code)).toContain('bad_amount')
  })

  test('a teammate decision of UNIDENTIFIED without category exports with it', async () => {
    const { db } = await setup()
    await importDecisions(db, 'bank_txn_id,item_id,amount,category,exception\nT5,,,,UNIDENTIFIED')
    await buildExportRows(db, { includeProposals: true, minConfidence: 0 })
    expect(await csvLines(db)).toContain('T5,,,UNIDENTIFIED,UNIDENTIFIED')
  })

  test('says which movements an import touched, so nothing stale stays around them', async () => {
    const { db } = await setup()
    const result = await importDecisions(db, `bank_txn_id,item_id,amount,category,exception\nT2,${A2},100,,`)
    expect(result.rids).toEqual([2])
  })

  test('import keeps what is already decided here', async () => {
    const { db } = await setup()
    await writeDecision(db, 1, {
      txnId: 'T1',
      allocations: [{ itemId: A1, amount: 100, exception: null }],
      category: null,
      exception: null,
      source: 'manual',
      at: 1,
    })
    const result = await importDecisions(
      db,
      `bank_txn_id,item_id,amount,category,exception\nT1,${A2},100,,\nT2,${A2},100,,\nT404,${A2},1,,`,
    )
    expect(result).toMatchObject({ applied: 1, skipped: 1, unknown: 1 })
    const [row] = await db.query(`SELECT item_id FROM decision_rows WHERE txn_id = 'T1'`)
    expect(row.item_id).toBe(A1)
  })
})

describe('dashboard and score', () => {
  test('counts coverage by state', async () => {
    const { db } = await setup()
    await writeDecision(db, 1, {
      txnId: 'T1',
      allocations: [{ itemId: A1, amount: 100, exception: null }],
      category: null,
      exception: null,
      source: 'confirmed',
      at: 1,
    })
    const stats = await loadDashboard(db)
    expect(stats.coverage).toEqual({ total: 5, reviewed: 1, bulk: 0, proposalOnly: 2, uncovered: 2 })
    expect(stats.currencies.find((c) => c.currency === 'EUR')?.reconciled).toBe(100)
  })

  test('scores the export against the golden, every line counted', async () => {
    const { db } = await setup()
    await buildExportRows(db, { includeProposals: true, minConfidence: 0 })
    const partial = await scoreAgainstGolden(db)
    expect(partial.allocationF1).toBe(1)
    expect(partial.transactionAccuracy).toBeCloseTo(4 / 5, 5)
    expect(partial.segments.nonitem).toEqual({ n: 1, accuracy: 0 })
    await writeDecision(db, 4, { txnId: 'T4', allocations: [], category: 'BANK_FEES', exception: null, source: 'manual', at: 1 })
    await buildExportRows(db, { includeProposals: true, minConfidence: 0 })
    expect((await scoreAgainstGolden(db)).total).toBe(100)
  })
})

describe('diagnosis', () => {
  const options = { includeProposals: true, minConfidence: 0 }
  const kinds = async (db: Db) => {
    await buildExportRows(db, options)
    await buildDiagnosis(db)
    const rows = await db.query('SELECT txn, kind, segment FROM diag ORDER BY txn')
    return Object.fromEntries(rows.map((r) => [r.txn as string, r.kind as string]))
  }

  test('classifies each line the way the scorer would', async () => {
    const { db } = await setup({
      'propuestas.csv': `bank_txn_id,item_id,amount,category,exception,confidence,motivo
T1,${A1},100.00,,,0.9,x
T2,${A2},100.00,,,0.9,x
T2,${A3},40.00,,,0.9,x
T3,${P1},80.00,,PARTIAL,0.9,x
T4,,,PAYROLL,,0.9,x`,
    })
    expect(await kinds(db)).toEqual({ T1: 'ok', T2: 'wrong_amount', T3: 'wrong_exception', T4: 'wrong_category', T5: 'ok' })
  })

  test('separates a line with no proposal, unless its automatic UNIDENTIFIED was right', async () => {
    const { db } = await setup()
    const result = await kinds(db)
    expect(result.T4).toBe('no_row')
    expect(result.T5).toBe('ok')
  })

  test('reports a wrong item', async () => {
    const { db } = await setup({ 'propuestas.csv': `bank_txn_id,item_id,amount,category,exception,confidence,motivo
T1,${A2},100.00,,,0.9,x` })
    expect((await kinds(db)).T1).toBe('wrong_item')
  })

  test('agrees with the score on how many lines are right', async () => {
    const { db } = await setup()
    await buildExportRows(db, options)
    await buildDiagnosis(db)
    const score = await scoreAgainstGolden(db)
    const [row] = await db.query(`SELECT count(*) FILTER (WHERE kind = 'ok') AS ok, count(*) AS n FROM diag`)
    expect((row.ok as number) / (row.n as number)).toBeCloseTo(score.transactionAccuracy, 6)
  })

  test('the errors view lists what does not match, and a segment narrows it', async () => {
    const { db } = await setup()
    await buildExportRows(db, options)
    await buildDiagnosis(db)
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, view: 'errors' })).toBe(1)
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, view: 'errors', segment: 'nonitem' })).toBe(1)
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, view: 'errors', segment: 'ar_1to1' })).toBe(0)
    expect((await viewCounts(db, DEFAULT_FILTERS, true)).errors).toBe(1)
  })

  test('reports segments with the most lost lines first', async () => {
    const { db } = await setup()
    await buildExportRows(db, options)
    await buildDiagnosis(db)
    const report = await segmentReport(db)
    expect(report.find((r) => r.segment === 'ar_1to1')).toMatchObject({ lines: 1, correct: 1, accuracy: 1 })
    expect(report.find((r) => r.segment === 'nonitem')).toMatchObject({ lines: 1, correct: 0 })
    expect(report.at(-1)?.accuracy).toBe(1)
  })

  test('exports the errors for whoever works on the pipeline', async () => {
    const { db } = await setup()
    await buildExportRows(db, options)
    await buildDiagnosis(db)
    const text = new TextDecoder().decode(await exportErrorsCsv(db))
    expect(text.split('\n')[0]).toBe('bank_txn_id,segment,difficulty,kind,amount,currency,description')
    expect(text).toContain('T4,nonitem')
  })

  test('a case brings its golden rows when there is a golden', async () => {
    const { db, meta } = await setup()
    const data = await loadCase(db, meta, 2)
    expect(data.golden.map((g) => g.item)).toEqual([A2, A3])
    expect(data.golden[0].segment).toBe('ar_1toN')
  })
})
