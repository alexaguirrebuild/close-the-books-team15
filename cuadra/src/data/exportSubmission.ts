import { EXCEPTIONS } from '../domain/types'
import { copyToBytes, lit, numOr, sqlNum, str, type Db } from './db'

export const SUBMISSION_HEADER = 'bank_txn_id,item_id,amount,category,exception'

/**
 * LIVE and FINAL are scored by the team's engine, so no submission file leaves this tool for them.
 * It is an allowlist: a folder opened without a known phase could be either of them.
 */
export const canDownloadSubmission = (phase: string | null): boolean => phase === 'train' || phase === 'sample'

export type ExportOptions = { includeProposals: boolean; minConfidence: number }

export const DEFAULT_EXPORT_OPTIONS: ExportOptions = { includeProposals: true, minConfidence: 0 }

/** The minimum confidence for a percentage typed by hand, limited to 0-100; null if the field holds no usable number. */
export function minConfidenceFromPercent(raw: string): number | null {
  const percent = raw.trim() ? Number(raw) : Number.NaN
  return Number.isFinite(percent) ? Math.min(Math.max(percent, 0), 100) / 100 : null
}

/** The percentage a field shows for a minimum confidence: with the decimals typed, without the noise of floating point. */
export function percentFromMinConfidence(confidence: number): string {
  return String(Number((confidence * 100).toFixed(6)))
}

export type ExportSummary = {
  rows: number
  txns: number
  fromReview: number
  fromProposals: number
  autoUnidentified: number
  /** Size of the file uncompressed, which is what the server limits. */
  bytes: number
}

export type ValidationSample = { line: number; txn: string; detail: string }
export type ValidationIssue = {
  code: string
  severity: 'error' | 'warning'
  title: string
  count: number
  samples: ValidationSample[]
}

const SAMPLE_LIMIT = 50
/** Four commas and the end of line of each row. */
const FIELD_SEPARATORS = 5
const MAX_BYTES = 60 * 1024 * 1024
const MAX_ROWS = 3_000_000
const ITEM_ID = '(ARI|ARC|API|APC)-[0-9]{4}-[0-9]{7}'
const UNIDENTIFIED = lit('UNIDENTIFIED')
const ROW_BYTES = `length(txn_id) + length(coalesce(item_id, '')) + length(amount) + length(coalesce(category, '')) + length(coalesce(exception, '')) + ${FIELD_SEPARATORS}`

/** Builds share working tables, so overlapping requests must wait for each other. */
let exportQueue: Promise<unknown> = Promise.resolve()

/**
 * Priority per movement: reviewed decision, then the proposal if allowed,
 * then UNIDENTIFIED, so no movement is left without a row.
 */
export function buildExportRows(db: Db, options: ExportOptions): Promise<ExportSummary> {
  const run = exportQueue.then(() => build(db, options))
  exportQueue = run.catch(() => undefined)
  return run
}

async function build(db: Db, options: ExportOptions): Promise<ExportSummary> {
  const proposalFilter = options.includeProposals
    ? `(t.conf >= ${sqlNum(options.minConfidence)}${options.minConfidence <= 0 ? ' OR t.conf IS NULL' : ''})`
    : 'false'
  await db.query(
    `CREATE OR REPLACE TABLE export_src AS
     WITH decided AS (
       SELECT rid, txn_id, item_id, amount,
              CASE WHEN item_id IS NULL THEN coalesce(category, CASE WHEN exception = ${UNIDENTIFIED} THEN ${UNIDENTIFIED} END) END AS category,
              exception, 'review' AS origin
       FROM decision_rows
     ), proposed AS (
       SELECT p.rid, p.txn_id, p.item_id,
              coalesce(p.amount, i.open_amount) AS amount,
              CASE WHEN p.item_id IS NULL THEN coalesce(p.category, CASE WHEN p.exception = ${UNIDENTIFIED} THEN ${UNIDENTIFIED} END) END AS category,
              p.exception, 'proposal' AS origin
       FROM prop p
       JOIN txn_q t ON t.rid = p.rid
       LEFT JOIN items i ON i.item_id = p.item_id
       WHERE ${proposalFilter} AND NOT EXISTS (SELECT 1 FROM decision_rows d WHERE d.rid = p.rid)
     ), combined AS (
       SELECT * FROM decided UNION ALL SELECT * FROM proposed
     ), missing AS (
       SELECT t.rid, t.id AS txn_id, NULL::VARCHAR AS item_id, NULL::DOUBLE AS amount, ${UNIDENTIFIED} AS category,
              ${UNIDENTIFIED} AS exception, 'auto' AS origin
       FROM txn t WHERE NOT EXISTS (SELECT 1 FROM combined c WHERE c.rid = t.rid)
     )
     SELECT * FROM combined UNION ALL SELECT * FROM missing`,
  )
  await db.query(
    `CREATE OR REPLACE TABLE export_rows AS
     SELECT CAST(row_number() OVER (ORDER BY s.rid, s.item_id NULLS LAST) + 1 AS INTEGER) AS line,
            s.rid, s.txn_id, s.item_id, s.origin, s.category, s.exception,
            CASE WHEN s.item_id IS NOT NULL THEN abs(s.amount) END AS amount_num,
            CASE WHEN s.item_id IS NULL OR s.amount IS NULL THEN '' ELSE printf('%.2f', abs(s.amount)) END AS amount
     FROM export_src s`,
  )
  await db.query('DROP TABLE export_src')
  const [summary] = await db.query(
    `SELECT count(*) AS rows_n, count(DISTINCT txn_id) AS txns,
            count(DISTINCT txn_id) FILTER (WHERE origin = 'review') AS review,
            count(DISTINCT txn_id) FILTER (WHERE origin = 'proposal') AS proposal,
            count(DISTINCT txn_id) FILTER (WHERE origin = 'auto') AS auto,
            CAST(sum(${ROW_BYTES}) AS BIGINT) AS bytes
     FROM export_rows`,
  )
  return {
    rows: numOr(summary?.rows_n, 0),
    txns: numOr(summary?.txns, 0),
    fromReview: numOr(summary?.review, 0),
    fromProposals: numOr(summary?.proposal, 0),
    autoUnidentified: numOr(summary?.auto, 0),
    bytes: numOr(summary?.bytes, 0) + SUBMISSION_HEADER.length + 1,
  }
}

type Check = {
  code: string
  severity: 'error' | 'warning'
  title: string
  sql: string
}

/**
 * Errors are only what the server rejects (FORMAT.md, starter/bankrec_score.py), so a valid
 * file can never be blocked here. What is merely suspicious stays a warning.
 */
function checks(categories: string[]): Check[] {
  const closed = EXCEPTIONS.map((e) => lit(e)).join(',')
  const categoryList = categories.map((c) => lit(c)).join(',')
  const hasCategories = categories.length > 0
  const base: Check[] = [
    {
      code: 'unknown_txn',
      severity: 'error',
      title: 'bank_txn_id que no existe',
      sql: `SELECT line, txn_id AS txn, txn_id AS detail FROM export_rows e WHERE rid IS NULL OR NOT EXISTS (SELECT 1 FROM txn t WHERE t.rid = e.rid)`,
    },
    {
      // Not an error: the server does not check it, and a return or a duplicate can hit an item settled in an earlier phase.
      code: 'unknown_item',
      severity: 'warning',
      title: 'Partida que no está en esta fase: normal en devoluciones y duplicados de otra anterior; si no es el caso, revísala',
      sql: `SELECT line, txn_id AS txn, item_id AS detail FROM export_rows e WHERE item_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM items i WHERE i.item_id = e.item_id)`,
    },
    {
      code: 'duplicate_pair',
      severity: 'error',
      title: 'Par (movimiento, partida) duplicado',
      sql: `SELECT line, txn_id AS txn, item_id AS detail FROM (
              SELECT line, txn_id, item_id, count(*) OVER (PARTITION BY txn_id, item_id) AS n FROM export_rows WHERE item_id IS NOT NULL
            ) WHERE n > 1`,
    },
    {
      code: 'bad_item_id',
      severity: 'error',
      title: 'item_id con un formato que el servidor rechaza',
      sql: `SELECT line, txn_id AS txn, item_id AS detail FROM export_rows WHERE item_id IS NOT NULL AND NOT regexp_full_match(item_id, '${ITEM_ID}')`,
    },
    {
      code: 'bad_amount',
      severity: 'error',
      title: 'Partida sin importe válido',
      sql: `SELECT line, txn_id AS txn, coalesce(nullif(amount, ''), 'vacío') AS detail FROM export_rows WHERE item_id IS NOT NULL AND NOT regexp_full_match(amount, '[0-9]+\\.[0-9]{2}')`,
    },
    {
      code: 'duplicate_no_item',
      severity: 'error',
      title: 'Más de una fila sin partida en un movimiento',
      sql: `SELECT line, txn_id AS txn, coalesce(category, '') AS detail FROM (
              SELECT line, txn_id, category, count(*) OVER (PARTITION BY txn_id) AS n FROM export_rows WHERE item_id IS NULL
            ) WHERE n > 1`,
    },
    {
      code: 'items_and_category',
      severity: 'error',
      title: 'Movimiento con partidas y fila sin partida a la vez',
      sql: `SELECT min(line) AS line, txn_id AS txn, txn_id AS detail FROM export_rows
            GROUP BY txn_id HAVING count(item_id) > 0 AND count(*) > count(item_id)`,
    },
    {
      code: 'too_many_rows',
      severity: 'error',
      title: 'El fichero supera los 3 millones de filas',
      sql: `SELECT 0 AS line, '' AS txn, CAST(count(*) AS VARCHAR) AS detail FROM export_rows HAVING count(*) > ${MAX_ROWS}`,
    },
    {
      code: 'too_large',
      severity: 'error',
      title: 'El fichero supera los 60 MB sin comprimir',
      sql: `SELECT 0 AS line, '' AS txn, CAST(sum(${ROW_BYTES}) AS VARCHAR) AS detail FROM export_rows HAVING sum(${ROW_BYTES}) > ${MAX_BYTES}`,
    },
    {
      code: 'category_with_item',
      severity: 'error',
      title: 'category en una fila con partida',
      sql: `SELECT line, txn_id AS txn, category AS detail FROM export_rows WHERE item_id IS NOT NULL AND category IS NOT NULL`,
    },
    {
      code: 'unknown_exception',
      severity: 'error',
      title: 'Excepción fuera de la lista cerrada',
      sql: `SELECT line, txn_id AS txn, exception AS detail FROM export_rows WHERE exception IS NOT NULL AND exception NOT IN (${closed})`,
    },
    {
      code: 'empty_row',
      severity: 'warning',
      title: 'Fila sin partida, categoría ni excepción: el servidor la acepta, pero contará como fallo',
      sql: `SELECT line, txn_id AS txn, '' AS detail FROM export_rows WHERE item_id IS NULL AND category IS NULL AND exception IS NULL`,
    },
    {
      code: 'coverage',
      severity: 'error',
      title: 'Movimiento sin fila en la exportación',
      sql: `SELECT 0 AS line, t.id AS txn, t.id AS detail FROM txn t WHERE NOT EXISTS (SELECT 1 FROM export_rows e WHERE e.rid = t.rid)`,
    },
    {
      code: 'over_allocated',
      severity: 'warning',
      title: 'Partida aplicada por encima de su saldo',
      sql: `SELECT min(e.line) AS line, min(e.txn_id) AS txn, e.item_id AS detail
            FROM export_rows e JOIN items i ON i.item_id = e.item_id
            GROUP BY e.item_id, i.open_amount
            HAVING sum(abs(e.amount_num)) > abs(i.open_amount) + greatest(0.01, abs(i.open_amount) * 0.005)
               AND bool_and(coalesce(e.exception, '') <> 'DUPLICATE_PAYMENT')`,
    },
    {
      code: 'unbalanced',
      severity: 'warning',
      title: 'No cuadra y no lleva excepción',
      sql: `SELECT min(e.line) AS line, e.txn_id AS txn, printf('%.2f frente a %.2f', sum(CASE WHEN i.kind = 'CN' THEN -abs(e.amount_num) ELSE abs(e.amount_num) END), abs(any_value(t.amount))) AS detail
            FROM export_rows e
            JOIN txn t ON t.rid = e.rid
            JOIN items i ON i.item_id = e.item_id
            WHERE i.currency = t.currency
            GROUP BY e.txn_id
            HAVING bool_and(e.exception IS NULL) AND bool_or(i.kind <> 'CN') AND abs(sum(CASE WHEN i.kind = 'CN' THEN -abs(e.amount_num) ELSE abs(e.amount_num) END) - abs(any_value(t.amount))) > greatest(0.01, abs(any_value(t.amount)) * 0.005)`,
    },
  ]
  if (!hasCategories) return base
  return [
    ...base,
    {
      code: 'unknown_category',
      severity: 'error',
      title: 'category que no está en categories.csv',
      sql: `SELECT line, txn_id AS txn, category AS detail FROM export_rows WHERE category IS NOT NULL AND category NOT IN (${categoryList})`,
    },
  ]
}

export async function validateExport(db: Db, categories: string[]): Promise<ValidationIssue[]> {
  const issues: ValidationIssue[] = []
  for (const check of checks(categories)) {
    const [countRow] = await db.query(`SELECT count(*) AS n FROM (${check.sql})`)
    const count = numOr(countRow?.n, 0)
    if (!count) continue
    const samples = await db.query(`SELECT * FROM (${check.sql}) ORDER BY line LIMIT ${SAMPLE_LIMIT}`)
    issues.push({
      code: check.code,
      severity: check.severity,
      title: check.title,
      count,
      samples: samples.map((s) => ({ line: numOr(s.line, 0), txn: str(s.txn), detail: str(s.detail) })),
    })
  }
  return issues
}

export function writeSubmission(db: Db): Promise<Uint8Array> {
  return copyToBytes(
    db,
    `SELECT txn_id AS bank_txn_id, item_id, nullif(amount, '') AS amount, category, exception FROM export_rows ORDER BY line`,
  )
}

export async function previewExport(db: Db, limit: number): Promise<Record<string, string>[]> {
  const rows = await db.query(
    `SELECT line, txn_id, item_id, amount, category, exception, origin FROM export_rows ORDER BY line LIMIT ${limit}`,
  )
  return rows.map((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, str(v)])))
}
