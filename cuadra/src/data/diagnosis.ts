import type { DiagnosisKind } from '../domain/goldenDiff'
import { copyToBytes, numOr, numOrNull, str, strOrNull, type Db } from './db'

export type SegmentRow = { segment: string; lines: number; correct: number; accuracy: number; kinds: Partial<Record<DiagnosisKind, number>>; difficulty: number | null }

/**
 * Compares what would be exported now with the golden, line by line, with the
 * rules of the scorer. Needs export_rows, so call it after buildExportRows.
 */
export async function buildDiagnosis(db: Db): Promise<void> {
  await db.query(
    `CREATE OR REPLACE TABLE diag AS
     WITH gold AS (
       SELECT txn, coalesce(item, '') AS k, item, amount, category, exception FROM golden
     ), pred AS (
       SELECT txn_id AS txn, coalesce(item_id, '') AS k, item_id AS item, amount_num AS amount, category, exception, origin FROM export_rows
     ), joined AS (
       SELECT coalesce(g.txn, p.txn) AS txn, g.txn IS NOT NULL AS in_gold, p.txn IS NOT NULL AS in_pred, g.item AS gold_item,
              g.amount AS gold_amount, p.amount AS pred_amount, g.category AS gold_category, p.category AS pred_category,
              coalesce(g.exception, '') AS gold_exception, coalesce(p.exception, '') AS pred_exception, p.origin
       FROM gold g FULL JOIN pred p ON g.txn = p.txn AND g.k = p.k
     ), lines AS (
       SELECT txn,
              bool_or(origin = 'auto') AS auto,
              bool_or(NOT in_gold OR NOT in_pred) AS wrong_item,
              bool_or(in_gold AND in_pred AND gold_item IS NOT NULL AND abs(pred_amount - gold_amount) > greatest(0.01, 0.005 * abs(gold_amount)) + 1e-9) AS wrong_amount,
              bool_or(in_gold AND in_pred AND gold_item IS NULL AND coalesce(gold_category, '') <> coalesce(pred_category, '')) AS wrong_category,
              bool_or(in_gold AND in_pred AND gold_exception <> pred_exception) AS wrong_exception
       FROM joined GROUP BY txn
     ), labels AS (
       SELECT txn, any_value(segment) AS segment, any_value(difficulty) AS difficulty FROM golden GROUP BY txn
     )
     -- A line with nothing to propose still goes out as UNIDENTIFIED, which may be right: the scorer counts it.
     SELECT t.rid, t.id AS txn, coalesce(s.segment, 'unlabelled') AS segment, s.difficulty,
            CASE WHEN l.txn IS NULL THEN 'no_row'
                 WHEN NOT (l.wrong_item OR l.wrong_amount OR l.wrong_category OR l.wrong_exception) THEN 'ok'
                 WHEN l.auto THEN 'no_row'
                 WHEN l.wrong_item THEN 'wrong_item'
                 WHEN l.wrong_amount THEN 'wrong_amount'
                 WHEN l.wrong_category THEN 'wrong_category'
                 ELSE 'wrong_exception' END AS kind
     FROM txn t
     LEFT JOIN lines l ON l.txn = t.id
     LEFT JOIN labels s ON s.txn = t.id`,
  )
}

/** Worst first: where the most lines are being lost is where fixing the pipeline pays most. */
export async function segmentReport(db: Db): Promise<SegmentRow[]> {
  const rows = await db.query(
    `SELECT segment, count(*) AS lines, count(*) FILTER (WHERE kind = 'ok') AS correct, min(difficulty) AS difficulty,
            count(*) FILTER (WHERE kind = 'wrong_item') AS wrong_item,
            count(*) FILTER (WHERE kind = 'wrong_amount') AS wrong_amount,
            count(*) FILTER (WHERE kind = 'wrong_exception') AS wrong_exception,
            count(*) FILTER (WHERE kind = 'wrong_category') AS wrong_category,
            count(*) FILTER (WHERE kind = 'no_row') AS no_row
     FROM diag GROUP BY segment ORDER BY count(*) - count(*) FILTER (WHERE kind = 'ok') DESC, segment`,
  )
  return rows.map((r) => {
    const lines = numOr(r.lines, 0)
    const correct = numOr(r.correct, 0)
    return {
      segment: str(r.segment),
      lines,
      correct,
      accuracy: lines ? correct / lines : 0,
      difficulty: numOrNull(r.difficulty),
      kinds: {
        wrong_item: numOr(r.wrong_item, 0),
        wrong_amount: numOr(r.wrong_amount, 0),
        wrong_exception: numOr(r.wrong_exception, 0),
        wrong_category: numOr(r.wrong_category, 0),
        no_row: numOr(r.no_row, 0),
      },
    }
  })
}

export function exportErrorsCsv(db: Db): Promise<Uint8Array> {
  return copyToBytes(
    db,
    `SELECT g.txn AS bank_txn_id, g.segment, g.difficulty, g.kind, t.amount, t.currency, t.description
     FROM diag g JOIN txn t ON t.rid = g.rid WHERE g.kind <> 'ok' ORDER BY g.segment, g.txn`,
  )
}

export async function goldenRowsFor(db: Db, txnId: string, literal: (value: string) => string) {
  const rows = await db.query(
    `SELECT item, amount, exception, category, segment, difficulty FROM golden WHERE txn = ${literal(txnId)}`,
    'high',
  )
  return rows.map((r) => ({
    item: strOrNull(r.item),
    amount: numOrNull(r.amount),
    exception: strOrNull(r.exception),
    category: strOrNull(r.category),
    segment: str(r.segment),
    difficulty: numOrNull(r.difficulty),
  }))
}
