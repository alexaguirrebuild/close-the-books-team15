import { tolerance } from './money'

export type ScoreRow = {
  txn: string
  item: string | null
  amount: number | null
  exception: string | null
  category: string | null
  segment?: string | null
}

export type SegmentScore = { n: number; accuracy: number }

export type ScoreResult = {
  /** 0 to 100 with two decimals, as the leaderboard shows it. */
  total: number
  allocationF1: number
  allocationPrecision: number
  allocationRecall: number
  transactionAccuracy: number
  exceptionMacroF1: number
  goldTransactions: number
  goldAllocations: number
  segments: Record<string, SegmentScore>
}

const WEIGHTS = { allocation: 0.5, transaction: 0.3, exception: 0.2 }
const UNLABELLED = 'unlabelled'

type GoldLine = {
  items: Map<string, { amount: number; exception: string }>
  category: string
  exception: string
  segment: string
}

type LineState = { itemRows: number; okRows: number; hasNoItem: boolean; category: string; exception: string }
type ClassCount = { tp: number; fp: number }

const ratio = (part: number, whole: number) => (whole ? part / whole : 0)
const harmonic = (p: number, r: number) => (p + r ? (2 * p * r) / (p + r) : 0)
const amountMatches = (pred: number, gold: number) => Math.abs(pred - gold) <= tolerance(gold) + 1e-9

function goldLines(rows: ScoreRow[], txnIds: string[]): Map<string, GoldLine> {
  const lines = new Map<string, GoldLine>(
    txnIds.map((id) => [id, { items: new Map(), category: '', exception: '', segment: UNLABELLED }]),
  )
  for (const row of rows) {
    const line = lines.get(row.txn) ?? { items: new Map(), category: '', exception: '', segment: UNLABELLED }
    lines.set(row.txn, line)
    if (row.segment) line.segment = row.segment
    if (row.item) line.items.set(row.item, { amount: row.amount ?? 0, exception: row.exception ?? '' })
    else {
      line.category = row.category ?? ''
      line.exception = row.exception ?? ''
    }
  }
  return lines
}

function isLineCorrect(line: GoldLine, state: LineState | undefined): boolean {
  if (!state) return false
  if (line.items.size) return !state.hasNoItem && state.itemRows === line.items.size && state.okRows === line.items.size
  return state.hasNoItem && state.itemRows === 0 && state.category === line.category && state.exception === line.exception
}

function macroF1(lines: Map<string, GoldLine>, counts: Map<string, ClassCount>): number {
  const goldCount = new Map<string, number>()
  const add = (code: string) => {
    if (code) goldCount.set(code, (goldCount.get(code) ?? 0) + 1)
  }
  for (const line of lines.values()) {
    if (line.items.size) line.items.forEach((item) => add(item.exception))
    else add(line.exception)
  }
  if (!goldCount.size) return 1
  const f1s = [...goldCount].map(([code, gold]) => {
    const { tp, fp } = counts.get(code) ?? { tp: 0, fp: 0 }
    return harmonic(ratio(tp, tp + fp), ratio(tp, gold))
  })
  return f1s.reduce((sum, value) => sum + value, 0) / f1s.length
}

/**
 * Port of starter/bankrec_score.py, the code the server runs. Assumes a file that
 * already passes validation, which is what the export screen checks first.
 * txnIds lists every bank line of the phase: a line nobody mentions still counts.
 */
export function computeScore(predRows: ScoreRow[], goldRows: ScoreRow[], txnIds: string[] = []): ScoreResult {
  const lines = goldLines(goldRows, txnIds)
  const states = new Map<string, LineState>()
  const counts = new Map<string, ClassCount>()
  const countOf = (code: string) => {
    const existing = counts.get(code)
    if (existing) return existing
    const created = { tp: 0, fp: 0 }
    counts.set(code, created)
    return created
  }
  let truePositives = 0
  let submittedPairs = 0

  for (const row of predRows) {
    const line = lines.get(row.txn)
    if (!line) continue
    const state = states.get(row.txn) ?? { itemRows: 0, okRows: 0, hasNoItem: false, category: '', exception: '' }
    states.set(row.txn, state)
    const exception = row.exception ?? ''
    if (row.item) {
      state.itemRows += 1
      submittedPairs += 1
      const gold = line.items.get(row.item)
      if (gold && amountMatches(row.amount ?? 0, gold.amount)) {
        truePositives += 1
        if (exception === gold.exception) state.okRows += 1
      }
      if (exception) {
        if (exception === gold?.exception) countOf(exception).tp += 1
        else countOf(exception).fp += 1
      }
    } else {
      state.hasNoItem = true
      state.category = row.category ?? ''
      state.exception = exception
      if (exception) {
        if (!line.items.size && exception === line.exception) countOf(exception).tp += 1
        else countOf(exception).fp += 1
      }
    }
  }

  const goldAllocations = [...lines.values()].reduce((sum, line) => sum + line.items.size, 0)
  const allocationPrecision = ratio(truePositives, submittedPairs)
  const allocationRecall = ratio(truePositives, goldAllocations)
  const allocationF1 = harmonic(allocationPrecision, allocationRecall)

  let correct = 0
  const segmentTotals = new Map<string, { n: number; ok: number }>()
  for (const [txn, line] of lines) {
    const ok = isLineCorrect(line, states.get(txn))
    if (ok) correct += 1
    const totals = segmentTotals.get(line.segment) ?? { n: 0, ok: 0 }
    segmentTotals.set(line.segment, { n: totals.n + 1, ok: totals.ok + (ok ? 1 : 0) })
  }

  const transactionAccuracy = ratio(correct, lines.size)
  const exceptionMacroF1 = macroF1(lines, counts)
  const weighted =
    WEIGHTS.allocation * allocationF1 + WEIGHTS.transaction * transactionAccuracy + WEIGHTS.exception * exceptionMacroF1

  return {
    total: Math.round(10000 * weighted) / 100,
    allocationF1,
    allocationPrecision,
    allocationRecall,
    transactionAccuracy,
    exceptionMacroF1,
    goldTransactions: lines.size,
    goldAllocations,
    segments: Object.fromEntries(
      [...segmentTotals].map(([segment, { n, ok }]) => [segment, { n, accuracy: ratio(ok, n) }]),
    ),
  }
}
