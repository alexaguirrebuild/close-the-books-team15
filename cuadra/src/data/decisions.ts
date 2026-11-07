import Papa from 'papaparse'
import type { Decision, ExceptionCode } from '../domain/types'
import { isException } from '../domain/types'
import { copyToBytes, lit, numOr, sqlNum, type Db } from './db'

export type LogEntry =
  | { kind: 'set'; decision: Decision }
  | { kind: 'clear'; txnId: string }
  | { kind: 'bulk'; batch: string; csv: string; source: 'bulk' | 'imported'; at: number }
  | { kind: 'unbulk'; batch: string }

const CSV_COLUMNS = ['bank_txn_id', 'item_id', 'amount', 'category', 'exception']

type CsvRow = Record<(typeof CSV_COLUMNS)[number], string>

function decisionsFromCsv(csv: string, source: 'bulk' | 'imported', batch: string, at: number): Decision[] {
  const parsed = Papa.parse<CsvRow>(csv, { header: true, skipEmptyLines: true })
  const byTxn = new Map<string, Decision>()
  for (const row of parsed.data) {
    const txnId = row.bank_txn_id
    if (!txnId) continue
    const exception = row.exception && isException(row.exception) ? (row.exception as ExceptionCode) : null
    const existing = byTxn.get(txnId) ?? {
      txnId,
      allocations: [],
      category: row.category || null,
      exception,
      source,
      at,
      batch,
    }
    byTxn.set(
      txnId,
      row.item_id
        ? { ...existing, allocations: [...existing.allocations, { itemId: row.item_id, amount: Number(row.amount) }] }
        : existing,
    )
  }
  return [...byTxn.values()]
}

/** The log is the source of truth across reloads; later entries win. */
export function replayLog(entries: LogEntry[]): Decision[] {
  const decisions = new Map<string, Decision>()
  for (const entry of entries) {
    if (entry.kind === 'set') decisions.set(entry.decision.txnId, entry.decision)
    if (entry.kind === 'clear') decisions.delete(entry.txnId)
    if (entry.kind === 'bulk') {
      for (const decision of decisionsFromCsv(entry.csv, entry.source, entry.batch, entry.at)) {
        decisions.set(decision.txnId, decision)
      }
    }
    if (entry.kind === 'unbulk') {
      for (const [txnId, decision] of decisions) {
        if (decision.batch === entry.batch) decisions.delete(txnId)
      }
    }
  }
  return [...decisions.values()]
}

function decisionToRows(decision: Decision): Record<string, unknown>[] {
  const base = {
    txn_id: decision.txnId,
    category: decision.category ?? '',
    exception: decision.exception ?? '',
    source: decision.source,
    batch: decision.batch ?? '',
    ts: decision.at,
  }
  if (!decision.allocations.length) return [{ ...base, item_id: '', amount: '' }]
  return decision.allocations.map((a) => ({ ...base, item_id: a.itemId, amount: a.amount }))
}

export async function loadDecisionsIntoDb(db: Db, decisions: Decision[]): Promise<void> {
  if (!decisions.length) return
  const name = `replay_${Date.now()}.csv`
  const columns = ['txn_id', 'item_id', 'amount', 'category', 'exception', 'source', 'batch', 'ts']
  await db.registerText(name, Papa.unparse(decisions.flatMap(decisionToRows), { columns }))
  await db.query(
    `INSERT INTO decision_rows
     SELECT t.rid, r.txn_id, nz(r.item_id), num(r.amount), nz(r.category), nz(r.exception), r.source, nz(r.batch), TRY_CAST(r.ts AS BIGINT)
     FROM read_csv('${name}', all_varchar=true, header=true, auto_detect=true) r
     JOIN txn t ON t.id = r.txn_id`,
  )
  await db.dropFile(name)
}

export async function writeDecision(db: Db, rid: number, decision: Decision): Promise<void> {
  const values = decisionToRows(decision)
    .map(
      (r) =>
        `(${rid}, ${lit(decision.txnId)}, ${lit((r.item_id as string) || null)}, ${sqlNum(typeof r.amount === 'number' ? r.amount : null)},
          ${lit(decision.category)}, ${lit(decision.exception)}, ${lit(decision.source)}, ${lit(decision.batch ?? null)}, ${decision.at})`,
    )
    .join(',')
  await db.query(`DELETE FROM decision_rows WHERE rid = ${rid}; INSERT INTO decision_rows VALUES ${values}`)
}

export async function clearDecision(db: Db, rid: number): Promise<void> {
  await db.query(`DELETE FROM decision_rows WHERE rid = ${rid}`)
}

export type BulkResult = { batch: string; count: number; csv: string; rids: number[] }

/** Confirms the proposal of every pending movement of the current queue at or above minConf. */
export async function confirmBulk(db: Db, minConf: number): Promise<BulkResult> {
  const batch = `bulk-${Date.now()}`
  const at = Date.now()
  await db.query(
    `CREATE OR REPLACE TABLE bulk_rows AS
     SELECT p.rid, p.txn_id, p.item_id,
            coalesce(p.amount, i.open_amount) AS amount,
            CASE WHEN p.item_id IS NULL THEN p.category END AS category,
            p.exception
     FROM q
     JOIN txn_q t ON t.rid = q.rid
     JOIN prop p ON p.rid = q.rid
     LEFT JOIN items i ON i.item_id = p.item_id
     WHERE t.conf >= ${sqlNum(minConf)}
       AND NOT EXISTS (SELECT 1 FROM decision_rows d WHERE d.rid = q.rid)`,
  )
  await db.query(
    `INSERT INTO decision_rows
     SELECT rid, txn_id, item_id, amount, category, exception, 'bulk', ${lit(batch)}, ${at} FROM bulk_rows`,
  )
  const csv = await bulkRowsCsv(db)
  const ridRows = await db.query('SELECT DISTINCT rid FROM bulk_rows')
  return { batch, count: ridRows.length, csv, rids: ridRows.map((r) => numOr(r.rid, 0)) }
}

async function bulkRowsCsv(db: Db): Promise<string> {
  const bytes = await copyToBytes(
    db,
    'SELECT txn_id AS bank_txn_id, item_id, amount, category, exception FROM bulk_rows ORDER BY rid',
  )
  return new TextDecoder().decode(bytes)
}

export async function undoBulk(db: Db, batch: string): Promise<void> {
  await db.query(`DELETE FROM decision_rows WHERE batch = ${lit(batch)}`)
}

export type ImportResult = { batch: string; applied: number; skipped: number; csv: string; unknown: number }

/** Merges the work of a teammate: movements already decided here keep their decision. */
export async function importDecisions(db: Db, text: string): Promise<ImportResult> {
  const name = `import_${Date.now()}.csv`
  const batch = `import-${Date.now()}`
  const at = Date.now()
  await db.registerText(name, text)
  await db.query(
    `CREATE OR REPLACE TABLE import_rows AS
     SELECT trim(bank_txn_id) AS txn_id, nz(item_id) AS item_id, num(amount) AS amount, nz(category) AS category, nz(exception) AS exception
     FROM read_csv('${name}', all_varchar=true, header=true, auto_detect=true)`,
  )
  const [counts] = await db.query(
    `SELECT count(DISTINCT i.txn_id) AS total,
            count(DISTINCT i.txn_id) FILTER (WHERE t.rid IS NULL) AS unknown,
            count(DISTINCT i.txn_id) FILTER (WHERE d.rid IS NOT NULL) AS skipped
     FROM import_rows i
     LEFT JOIN txn t ON t.id = i.txn_id
     LEFT JOIN (SELECT DISTINCT rid FROM decision_rows) d ON d.rid = t.rid`,
  )
  await db.query(
    `CREATE OR REPLACE TABLE bulk_rows AS
     SELECT t.rid, i.txn_id, i.item_id, i.amount, i.category, i.exception
     FROM import_rows i JOIN txn t ON t.id = i.txn_id
     WHERE NOT EXISTS (SELECT 1 FROM decision_rows d WHERE d.rid = t.rid)`,
  )
  await db.query(
    `INSERT INTO decision_rows
     SELECT rid, txn_id, item_id, amount, category, exception, 'imported', ${lit(batch)}, ${at} FROM bulk_rows`,
  )
  const csv = await bulkRowsCsv(db)
  await db.dropFile(name)
  const total = numOr(counts?.total, 0)
  const skipped = numOr(counts?.skipped, 0)
  const unknown = numOr(counts?.unknown, 0)
  return { batch, applied: total - skipped - unknown, skipped, unknown, csv }
}

export function exportReviewCsv(db: Db): Promise<Uint8Array> {
  return copyToBytes(
    db,
    'SELECT txn_id AS bank_txn_id, item_id, amount, category, exception FROM decision_rows ORDER BY rid',
  )
}
