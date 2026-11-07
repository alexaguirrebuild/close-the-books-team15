import { computeScore, type ScoreResult, type ScoreRow } from '../domain/score'
import { numOr, numOrNull, str, strOrNull, type Db, type Row } from './db'

export type CoverageStats = {
  total: number
  reviewed: number
  bulk: number
  proposalOnly: number
  uncovered: number
}

export type CurrencyStats = { currency: string; total: number; reconciled: number; pending: number; txns: number }
export type LabelCount = { label: string; count: number }
export type ConfidenceBin = { label: string; count: number }

export type DashboardStats = {
  coverage: CoverageStats
  currencies: CurrencyStats[]
  categories: LabelCount[]
  exceptions: LabelCount[]
  confidence: ConfidenceBin[]
  meanConfidence: number | null
}

const COUNT_LABELS = (rows: Record<string, unknown>[]): LabelCount[] =>
  rows.map((r) => ({ label: str(r.label), count: numOr(r.n, 0) }))

const CONFIDENCE_BINS = ['0–50 %', '50–70 %', '70–85 %', '85–95 %', '95–100 %']

export async function loadDashboard(db: Db): Promise<DashboardStats> {
  const [coverage] = await db.query(
    `SELECT count(*) AS total,
            count(*) FILTER (WHERE d.rid IS NOT NULL AND d.source NOT IN ('bulk')) AS reviewed,
            count(*) FILTER (WHERE d.source = 'bulk') AS bulk,
            count(*) FILTER (WHERE d.rid IS NULL AND t.n_items IS NOT NULL) AS proposal_only,
            count(*) FILTER (WHERE d.rid IS NULL AND t.n_items IS NULL) AS uncovered
     FROM txn_q t
     LEFT JOIN (SELECT rid, any_value(source) AS source FROM decision_rows GROUP BY rid) d ON d.rid = t.rid`,
  )
  const currencies = await db.query(
    `SELECT t.currency,
            count(*) AS txns,
            sum(t.abs_amount) AS total,
            sum(t.abs_amount) FILTER (WHERE d.rid IS NOT NULL) AS reconciled,
            sum(t.abs_amount) FILTER (WHERE d.rid IS NULL) AS pending
     FROM txn_q t
     LEFT JOIN (
       SELECT rid FROM decision_rows
       GROUP BY rid
       HAVING bool_or(item_id IS NOT NULL OR category IS NOT NULL OR coalesce(exception, '') <> 'UNIDENTIFIED')
     ) d ON d.rid = t.rid
     GROUP BY t.currency ORDER BY total DESC`,
  )
  const categories = await db.query(
    `SELECT category AS label, count(DISTINCT rid) AS n FROM (
       SELECT rid, category FROM decision_rows WHERE category IS NOT NULL
       UNION ALL
       SELECT p.rid, p.category FROM prop p WHERE p.category IS NOT NULL AND p.item_id IS NULL
         AND NOT EXISTS (SELECT 1 FROM decision_rows d WHERE d.rid = p.rid)
     ) GROUP BY 1 ORDER BY n DESC`,
  )
  const exceptions = await db.query(
    `SELECT exception AS label, count(DISTINCT rid) AS n FROM (
       SELECT rid, exception FROM decision_rows WHERE exception IS NOT NULL
       UNION ALL
       SELECT p.rid, p.exception FROM prop p WHERE p.exception IS NOT NULL
         AND NOT EXISTS (SELECT 1 FROM decision_rows d WHERE d.rid = p.rid)
     ) GROUP BY 1 ORDER BY n DESC`,
  )
  const bins = await db.query(
    `SELECT CASE WHEN conf < 0.5 THEN 0 WHEN conf < 0.7 THEN 1 WHEN conf < 0.85 THEN 2 WHEN conf < 0.95 THEN 3 ELSE 4 END AS bin,
            count(*) AS n
     FROM prop_sum WHERE conf IS NOT NULL GROUP BY 1`,
  )
  const [mean] = await db.query('SELECT avg(conf) AS m FROM prop_sum')
  const binCounts = new Map(bins.map((b) => [numOr(b.bin, 0), numOr(b.n, 0)]))
  const total = numOr(coverage?.total, 0)
  const reviewed = numOr(coverage?.reviewed, 0)
  const bulk = numOr(coverage?.bulk, 0)
  const proposalOnly = numOr(coverage?.proposal_only, 0)

  return {
    coverage: {
      total,
      reviewed,
      bulk,
      proposalOnly,
      uncovered: total - reviewed - bulk - proposalOnly,
    },
    currencies: currencies.map((c) => ({
      currency: str(c.currency),
      txns: numOr(c.txns, 0),
      total: numOr(c.total, 0),
      reconciled: numOr(c.reconciled, 0),
      pending: numOr(c.pending, 0),
    })),
    categories: COUNT_LABELS(categories),
    exceptions: COUNT_LABELS(exceptions),
    confidence: CONFIDENCE_BINS.map((label, index) => ({ label, count: binCounts.get(index) ?? 0 })),
    meanConfidence: numOrNull(mean?.m),
  }
}

const toScoreRow = (r: Row): ScoreRow => ({
  txn: str(r.txn),
  item: strOrNull(r.item),
  amount: numOrNull(r.amount),
  category: strOrNull(r.category),
  exception: strOrNull(r.exception),
  segment: strOrNull(r.segment),
})

/** Scores the rows that would be exported, the same ones a submission would contain. */
export async function scoreAgainstGolden(db: Db): Promise<ScoreResult> {
  const predicted = await db.query(
    'SELECT txn_id AS txn, item_id AS item, amount_num AS amount, category, exception FROM export_rows',
  )
  const golden = await db.query('SELECT txn, item, amount, category, exception, segment FROM golden')
  const lines = await db.query('SELECT id FROM txn')
  return computeScore(predicted.map(toScoreRow), golden.map(toScoreRow), lines.map((r) => str(r.id)))
}
