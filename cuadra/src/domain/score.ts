import { tolerance } from './money'

export type ScoreRow = {
  txn: string
  item: string | null
  amount: number | null
  exception: string | null
  category: string | null
}

export type ScoreResult = {
  total: number
  allocationF1: number
  allocationPrecision: number
  allocationRecall: number
  transactionAccuracy: number
  exceptionMacroF1: number
  goldTransactions: number
  goldAllocations: number
}

const WEIGHTS = { allocation: 0.5, transaction: 0.3, exception: 0.2 }

function f1(precision: number, recall: number): number {
  return precision + recall === 0 ? 0 : (2 * precision * recall) / (precision + recall)
}

function groupByTxn(rows: ScoreRow[]): Map<string, ScoreRow[]> {
  const groups = new Map<string, ScoreRow[]>()
  for (const row of rows) {
    const group = groups.get(row.txn)
    if (group) group.push(row)
    else groups.set(row.txn, [row])
  }
  return groups
}

function amountMatches(pred: number | null, gold: number | null): boolean {
  if (pred === null || gold === null) return false
  return Math.abs(pred - gold) <= tolerance(gold) + 1e-9
}

function exceptionOf(rows: ScoreRow[]): string | null {
  return rows.find((r) => r.exception)?.exception ?? null
}

function txnMatches(pred: ScoreRow[], gold: ScoreRow[]): boolean {
  if (pred.length !== gold.length) return false
  if (exceptionOf(pred) !== exceptionOf(gold)) return false
  return gold.every((g) =>
    pred.some((p) =>
      g.item
        ? p.item === g.item && amountMatches(p.amount, g.amount)
        : !p.item && (p.category ?? null) === (g.category ?? null),
    ),
  )
}

function exceptionMacroF1(pred: Map<string, ScoreRow[]>, gold: Map<string, ScoreRow[]>): number {
  const stats = new Map<string, { tp: number; fp: number; fn: number }>()
  const stat = (label: string) => {
    const existing = stats.get(label)
    if (existing) return existing
    const created = { tp: 0, fp: 0, fn: 0 }
    stats.set(label, created)
    return created
  }
  for (const txn of new Set([...pred.keys(), ...gold.keys()])) {
    const predLabel = exceptionOf(pred.get(txn) ?? [])
    const goldLabel = exceptionOf(gold.get(txn) ?? [])
    if (predLabel && predLabel === goldLabel) stat(predLabel).tp += 1
    else {
      if (predLabel) stat(predLabel).fp += 1
      if (goldLabel) stat(goldLabel).fn += 1
    }
  }
  if (stats.size === 0) return 1
  const f1s = [...stats.values()].map(({ tp, fp, fn }) =>
    f1(tp + fp === 0 ? 0 : tp / (tp + fp), tp + fn === 0 ? 0 : tp / (tp + fn)),
  )
  return f1s.reduce((sum, value) => sum + value, 0) / f1s.length
}

/**
 * Local calculation of the published formula:
 * 0.5 · Allocation F1 + 0.3 · Transaction accuracy + 0.2 · Exception macro-F1.
 * Not the official scorer: where the formula leaves room, the assumptions are
 * listed in the README so the result can be contrasted against it.
 */
export function computeScore(predRows: ScoreRow[], goldRows: ScoreRow[]): ScoreResult {
  const pred = groupByTxn(predRows)
  const gold = groupByTxn(goldRows)
  const predAllocations = predRows.filter((r) => r.item).length
  const goldAllocations = goldRows.filter((r) => r.item).length

  let truePositives = 0
  for (const g of goldRows) {
    if (!g.item) continue
    const hit = (pred.get(g.txn) ?? []).some(
      (p) => p.item === g.item && amountMatches(p.amount, g.amount),
    )
    if (hit) truePositives += 1
  }

  const allocationPrecision = predAllocations === 0 ? 0 : truePositives / predAllocations
  const allocationRecall = goldAllocations === 0 ? 0 : truePositives / goldAllocations
  const allocationF1 =
    goldAllocations === 0 && predAllocations === 0 ? 1 : f1(allocationPrecision, allocationRecall)

  let correctTxns = 0
  for (const [txn, goldGroup] of gold) {
    if (txnMatches(pred.get(txn) ?? [], goldGroup)) correctTxns += 1
  }
  const transactionAccuracy = gold.size === 0 ? 0 : correctTxns / gold.size
  const exceptionF1 = exceptionMacroF1(pred, gold)

  return {
    total:
      WEIGHTS.allocation * allocationF1 +
      WEIGHTS.transaction * transactionAccuracy +
      WEIGHTS.exception * exceptionF1,
    allocationF1,
    allocationPrecision,
    allocationRecall,
    transactionAccuracy,
    exceptionMacroF1: exceptionF1,
    goldTransactions: gold.size,
    goldAllocations,
  }
}
