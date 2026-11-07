import { createRequire } from 'node:module'
import { readdirSync, rmSync } from 'node:fs'
import path from 'node:path'
import { afterAll, describe, expect, test } from 'vitest'
import { normalizeRow, type Db, type Row } from './db'
import { loadCase } from './caseLoader'
import {
  confirmBulk,
  importDecisions,
  loadDecisionsIntoDb,
  replayLog,
  undoBulk,
  writeDecision,
  type LogEntry,
} from './decisions'
import { buildExportRows, SUBMISSION_HEADER, validateExport, writeSubmission } from './exportSubmission'
import { ingest, type DatasetMeta } from './ingest'
import { buildPlan, type SourceFile } from './preflight'
import { buildQueue, DEFAULT_FILTERS, queuePage } from './queue'
import { loadDashboard, scoreAgainstGolden } from './stats'

const require = createRequire(import.meta.url)
const duckdb = require('@duckdb/duckdb-wasm/dist/duckdb-node-blocking.cjs')

const FILES: Record<string, string> = {
  'bank_transactions.csv': `bank_txn_id,booking_date,amount,currency,account,description
T1,2024-02-01,100.00,EUR,ES01,PAGO FRA F-1001 ACME
T2,2024-02-02,150.00,EUR,ES01,TRANSFERENCIA ACME
T3,2024-02-03,-80.00,EUR,ES02,PAGO PROVEEDOR
T4,2024-02-04,-12.50,EUR,ES01,COMISION MANTENIMIENTO
T5,2024-02-05,999.00,USD,ES03,INGRESO DESCONOCIDO`,
  'ar_invoices.csv': `item_id,printed_number,customer_id,amount,currency,due_date
ARI-1,F-1001,C1,100.00,EUR,2024-01-10
ARI-2,F-1002,C1,100.00,EUR,2024-01-20
ARI-3,F-1003,C1,50.00,EUR,2024-01-25`,
  'ap_invoices.csv': `item_id,printed_number,supplier_id,amount,currency,due_date
API-1,P-900,S1,80.00,EUR,2024-01-15`,
  'credit_notes.csv': `item_id,printed_number,customer_id,amount,currency,due_date
ARC-1,N-1,C1,-10.00,EUR,2024-01-05`,
  'categories.csv': `category
BANK_FEES
PAYROLL`,
  'customers.csv': `customer_id,name
C1,ACME SA`,
  'suppliers.csv': `supplier_id,name
S1,Globex SL`,
  'fx_rates.csv': `date,base_currency,quote_currency,rate
2024-01-01,USD,EUR,0.9`,
  'propuestas.csv': `bank_txn_id,item_id,amount,exception,confidence,motivo
T1,ARI-1,100.00,,0.98,numero impreso
T2,ARI-2,100.00,,0.70,mismo tercero
T2,ARI-3,50.00,,0.70,mismo tercero
T3,API-1,80.00,,0.60,importe exacto`,
  'golden.csv': `bank_txn_id,item_id,amount,category,exception
T1,ARI-1,100.00,,
T2,ARI-2,100.00,,
T2,ARI-3,50.00,,
T3,API-1,80.00,,
T4,,12.50,BANK_FEES,
T5,,999.00,,UNIDENTIFIED`,
}

function createNodeDb(): Promise<Db> {
  const dist = path.dirname(require.resolve('@duckdb/duckdb-wasm/dist/duckdb-node-blocking.cjs'))
  const bundles = {
    mvp: { mainModule: path.join(dist, 'duckdb-mvp.wasm'), mainWorker: path.join(dist, 'duckdb-node-mvp.worker.cjs') },
    eh: { mainModule: path.join(dist, 'duckdb-eh.wasm'), mainWorker: path.join(dist, 'duckdb-node-eh.worker.cjs') },
  }
  return duckdb
    .createDuckDB(bundles, new duckdb.VoidLogger(), duckdb.NODE_RUNTIME)
    .then(async (db: Record<string, (...args: unknown[]) => unknown>) => {
      await db.instantiate()
      const conn = db.connect() as { query: (sql: string) => { toArray: () => { toJSON: () => Row }[] } }
      const text = async (file: File) => file.text()
      return {
        query: async (sql: string) => conn.query(sql).toArray().map((r) => normalizeRow(r.toJSON())),
        registerFile: async (name: string, file: File) => void db.registerFileText(name, await text(file)),
        registerText: async (name: string, content: string) => void db.registerFileText(name, content),
        readFile: async (name: string) => db.copyFileToBuffer(name) as Uint8Array,
        dropFile: async (name: string) => void db.dropFile(name),
      } satisfies Db
    })
}

function sources(overrides: Record<string, string | null> = {}): SourceFile[] {
  const merged = { ...FILES, ...overrides }
  return Object.entries(merged).flatMap(([name, content]) =>
    content === null ? [] : [{ path: name, file: new File([content], name) }],
  )
}

async function setup(overrides: Record<string, string | null> = {}): Promise<{ db: Db; meta: DatasetMeta }> {
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

describe('ingest', () => {
  test('reads the official files and resolves aliases', async () => {
    const { meta } = await setup()
    expect(meta.txnCount).toBe(5)
    expect(meta.itemCount).toBe(5)
    expect(meta.proposalTxnCount).toBe(3)
    expect(meta.categories).toEqual(['BANK_FEES', 'PAYROLL'])
    expect(meta.hasGolden).toBe(true)
  })

  test('marks only the items of open_items_at_start as open', async () => {
    const { meta } = await setup({ 'open_items_at_start.csv': 'item_id,open_amount\nARI-1,60.00' })
    expect(meta.openItemCount).toBe(1)
  })

  test('warns about proposals for movements that do not exist', async () => {
    const { meta } = await setup({ 'propuestas.csv': 'bank_txn_id,item_id,amount,exception,confidence,motivo\nT99,ARI-1,1,,0.5,x' })
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
      'propuestas.csv': 'bank_txn_id,item_id,amount,exception,confidence,motivo\nT1,ARI-1,100,,95,x',
    })
    const [row] = await db.query('SELECT conf FROM prop')
    expect(row.conf).toBeCloseTo(0.95)
  })
})

describe('queue', () => {
  test('orders by amount and hides resolved movements on rebuild', async () => {
    const { db } = await setup()
    expect(await buildQueue(db, DEFAULT_FILTERS)).toBe(5)
    expect((await queuePage(db, 0, 10))[0].id).toBe('T5')
    await writeDecision(db, 5, {
      txnId: 'T5',
      allocations: [],
      category: null,
      exception: 'UNIDENTIFIED',
      source: 'manual',
      at: 1,
    })
    expect(await buildQueue(db, DEFAULT_FILTERS)).toBe(4)
  })

  test('filters by case type and party', async () => {
    const { db } = await setup()
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, caseType: 'uno_a_n' })).toBe(1)
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, party: 'globex' })).toBe(1)
  })

  test('shows decided movements when asked, to correct them', async () => {
    const { db } = await setup()
    await writeDecision(db, 5, {
      txnId: 'T5',
      allocations: [],
      category: null,
      exception: 'RETURNED',
      source: 'manual',
      at: 1,
    })
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, status: 'decided' })).toBe(1)
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, status: 'all' })).toBe(5)
    expect((await queuePage(db, 0, 10))[0].resolved).toBe(true)
  })

  test('finds a movement by its id', async () => {
    const { db } = await setup()
    expect(await buildQueue(db, { ...DEFAULT_FILTERS, party: 't3' })).toBe(1)
  })

  test('sorts lowest confidence first when asked', async () => {
    const { db } = await setup()
    await buildQueue(db, { ...DEFAULT_FILTERS, sort: 'confidence_low', caseType: 'uno_a_uno' })
    expect((await queuePage(db, 0, 10))[0].id).toBe('T3')
  })
})

describe('loadCase', () => {
  test('finds the printed number, the exact amount and the party', async () => {
    const { db, meta } = await setup()
    const data = await loadCase(db, meta, 1)
    expect(data.raw.printed.map((p) => p.item.itemId)).toEqual(['ARI-1'])
    expect(data.raw.printed[0].fragment).toBe('F-1001')
    expect(data.raw.exact.map((i) => i.itemId)).toEqual(['ARI-1', 'ARI-2'])
    expect(data.raw.party.map((i) => i.itemId)).toContain('ARI-3')
  })

  test('finds a printed number split by the bank', async () => {
    const { db, meta } = await setup({
      'bank_transactions.csv': 'bank_txn_id,date,amount,currency,account,description\nT1,2024-02-01,100,EUR,A,ABONO F 1001',
    })
    const data = await loadCase(db, meta, 1)
    expect(data.raw.printed.map((p) => p.item.itemId)).toEqual(['ARI-1'])
    expect(data.matchedFragments).toEqual(['1001'])
  })

  test('finds the party by the name in the concept', async () => {
    const { db, meta } = await setup({ 'bank_transactions.csv': 'bank_txn_id,date,amount,currency,account,description\nT1,2024-02-01,1,EUR,A,ABONO ACME SA' })
    const data = await loadCase(db, meta, 1)
    expect(data.raw.party.length).toBeGreaterThan(0)
  })

  test('only offers items of the right side for the exact amount', async () => {
    const { db, meta } = await setup()
    const data = await loadCase(db, meta, 3)
    expect(data.raw.exact.map((i) => i.itemId)).toEqual(['API-1'])
  })
})

describe('export', () => {
  const options = { includeProposals: true, minConfidence: 0 }

  test('writes the exact header and marks the rest as UNIDENTIFIED', async () => {
    const { db } = await setup()
    const summary = await buildExportRows(db, options)
    const lines = await csvLines(db)
    expect(lines[0]).toBe(SUBMISSION_HEADER)
    expect(summary.txns).toBe(5)
    expect(summary.autoUnidentified).toBe(2)
    expect(lines).toContain('T5,,999.00,,UNIDENTIFIED')
    expect(lines).toContain('T1,ARI-1,100.00,,')
  })

  test('a reviewed decision beats the proposal', async () => {
    const { db } = await setup()
    await writeDecision(db, 1, {
      txnId: 'T1',
      allocations: [{ itemId: 'ARI-2', amount: 95 }],
      category: null,
      exception: 'SHORT_DISCOUNT',
      source: 'manual',
      at: 1,
    })
    await buildExportRows(db, options)
    const lines = await csvLines(db)
    expect(lines).toContain('T1,ARI-2,95.00,,SHORT_DISCOUNT')
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
    const issues = await validateExport(db, meta.categories)
    expect(issues.filter((i) => i.severity === 'error')).toEqual([])
  })

  test('reports an unknown item with its line number', async () => {
    const { db, meta } = await setup({
      'propuestas.csv': 'bank_txn_id,item_id,amount,exception,confidence,motivo\nT1,ARI-404,100,,0.9,x',
    })
    await buildExportRows(db, options)
    const issue = (await validateExport(db, meta.categories)).find((i) => i.code === 'unknown_item')
    expect(issue?.count).toBe(1)
    expect(issue?.samples[0]).toMatchObject({ line: 2, txn: 'T1', detail: 'ARI-404' })
  })

  test('reports duplicate pairs', async () => {
    const { db, meta } = await setup({
      'propuestas.csv': 'bank_txn_id,item_id,amount,exception,confidence,motivo\nT1,ARI-1,50,,0.9,x\nT1,ARI-1,50,,0.9,x',
    })
    await buildExportRows(db, options)
    const issue = (await validateExport(db, meta.categories)).find((i) => i.code === 'duplicate_pair')
    expect(issue?.count).toBe(2)
  })

  test('reports exceptions outside the closed list', async () => {
    const { db, meta } = await setup({
      'propuestas.csv': 'bank_txn_id,item_id,amount,exception,confidence,motivo\nT1,ARI-1,100,NOPE,0.9,x',
    })
    await buildExportRows(db, options)
    const issues = await validateExport(db, meta.categories)
    expect(issues.map((i) => i.code)).toContain('unknown_exception')
  })

  test('reports a category outside categories.csv', async () => {
    const { db, meta } = await setup()
    await writeDecision(db, 4, {
      txnId: 'T4',
      allocations: [],
      category: 'INVENTADA',
      exception: null,
      source: 'manual',
      at: 1,
    })
    await buildExportRows(db, options)
    const issues = await validateExport(db, meta.categories)
    expect(issues.map((i) => i.code)).toContain('unknown_category')
  })

  test('warns when an allocation does not add up without an exception', async () => {
    const { db, meta } = await setup()
    await writeDecision(db, 1, {
      txnId: 'T1',
      allocations: [{ itemId: 'ARI-1', amount: 60 }],
      category: null,
      exception: null,
      source: 'manual',
      at: 1,
    })
    await buildExportRows(db, options)
    const issue = (await validateExport(db, meta.categories)).find((i) => i.code === 'unbalanced')
    expect(issue?.severity).toBe('warning')
  })
})

describe('decisions', () => {
  test('bulk confirms only at or above the threshold and can be undone', async () => {
    const { db } = await setup()
    await buildQueue(db, DEFAULT_FILTERS)
    const result = await confirmBulk(db, 0.9)
    expect(result.count).toBe(1)
    const [after] = await db.query('SELECT count(DISTINCT rid) AS n FROM decision_rows')
    expect(after.n).toBe(1)
    await undoBulk(db, result.batch)
    const [undone] = await db.query('SELECT count(*) AS n FROM decision_rows')
    expect(undone.n).toBe(0)
  })

  test('replaying the log rebuilds the same decisions', async () => {
    const { db } = await setup()
    await buildQueue(db, DEFAULT_FILTERS)
    const bulk = await confirmBulk(db, 0.65)
    const entries: LogEntry[] = [
      { kind: 'bulk', batch: bulk.batch, csv: bulk.csv, source: 'bulk', at: 1 },
      {
        kind: 'set',
        decision: { txnId: 'T5', allocations: [], category: null, exception: 'RETURNED', source: 'manual', at: 2 },
      },
      { kind: 'clear', txnId: 'T1' },
    ]
    const decisions = replayLog(entries)
    expect(decisions.map((d) => d.txnId).sort()).toEqual(['T2', 'T5'])
    expect(decisions.find((d) => d.txnId === 'T2')?.allocations).toHaveLength(2)

    const fresh = await setup()
    await loadDecisionsIntoDb(fresh.db, decisions)
    const [row] = await fresh.db.query(`SELECT exception FROM decision_rows WHERE txn_id = 'T5'`)
    expect(row.exception).toBe('RETURNED')
  })

  test('replay drops a bulk batch that was undone', () => {
    const entries: LogEntry[] = [
      { kind: 'bulk', batch: 'b1', csv: 'bank_txn_id,item_id,amount,category,exception\nT1,A,1,,', source: 'bulk', at: 1 },
      { kind: 'unbulk', batch: 'b1' },
    ]
    expect(replayLog(entries)).toEqual([])
  })

  test('import keeps what is already decided here', async () => {
    const { db } = await setup()
    await writeDecision(db, 1, {
      txnId: 'T1',
      allocations: [{ itemId: 'ARI-1', amount: 100 }],
      category: null,
      exception: null,
      source: 'manual',
      at: 1,
    })
    const result = await importDecisions(
      db,
      'bank_txn_id,item_id,amount,category,exception\nT1,ARI-2,100,,\nT2,ARI-2,100,,\nT404,ARI-2,1,,',
    )
    expect(result).toMatchObject({ applied: 1, skipped: 1, unknown: 1 })
    const [row] = await db.query(`SELECT item_id FROM decision_rows WHERE txn_id = 'T1'`)
    expect(row.item_id).toBe('ARI-1')
  })
})

describe('dashboard and score', () => {
  test('counts coverage by state', async () => {
    const { db } = await setup()
    await writeDecision(db, 1, {
      txnId: 'T1',
      allocations: [{ itemId: 'ARI-1', amount: 100 }],
      category: null,
      exception: null,
      source: 'confirmed',
      at: 1,
    })
    const stats = await loadDashboard(db)
    expect(stats.coverage).toEqual({ total: 5, reviewed: 1, bulk: 0, proposalOnly: 2, uncovered: 2 })
    expect(stats.currencies.find((c) => c.currency === 'EUR')?.reconciled).toBe(100)
  })

  test('scores the export against the golden', async () => {
    const { db } = await setup()
    await buildExportRows(db, { includeProposals: true, minConfidence: 0 })
    const partial = await scoreAgainstGolden(db)
    expect(partial.allocationF1).toBe(1)
    expect(partial.transactionAccuracy).toBeCloseTo(4 / 5, 5)
    await writeDecision(db, 4, {
      txnId: 'T4',
      allocations: [],
      category: 'BANK_FEES',
      exception: null,
      source: 'manual',
      at: 1,
    })
    await buildExportRows(db, { includeProposals: true, minConfidence: 0 })
    expect((await scoreAgainstGolden(db)).total).toBeCloseTo(1)
  })
})
