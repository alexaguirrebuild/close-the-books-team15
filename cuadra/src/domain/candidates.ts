import { convert, tolerance } from './money'
import type { FxRate, Item } from './types'

export type CandidateReason = 'proposal' | 'printed' | 'exact' | 'converted' | 'party' | 'search'

export type RawCandidates = {
  proposal: Item[]
  printed: { item: Item; fragment: string }[]
  exact: Item[]
  party: Item[]
}

export type Candidate = {
  item: Item
  reasons: CandidateReason[]
  fragment: string | null
  fifo: boolean
  usedBy: string | null
}

const REASON_WEIGHT: Record<CandidateReason, number> = {
  printed: 80,
  proposal: 60,
  exact: 40,
  converted: 15,
  party: 10,
  search: 5,
}
const USED_PENALTY = 500
const CLOSED_PENALTY = 1000

function score(candidate: Candidate): number {
  const reasons = candidate.reasons.reduce((sum, r) => sum + REASON_WEIGHT[r], 0)
  const usedPenalty = candidate.usedBy ? USED_PENALTY : 0
  const closedPenalty = candidate.item.isOpen ? 0 : CLOSED_PENALTY
  return reasons + (candidate.fifo ? 20 : 0) - usedPenalty - closedPenalty
}

function collect(
  txnCurrency: string,
  raw: RawCandidates,
  used: Map<string, string>,
): Map<string, Candidate> {
  const merged = new Map<string, Candidate>()
  const add = (item: Item, reason: CandidateReason, fragment: string | null = null) => {
    const existing = merged.get(item.itemId)
    if (existing) {
      merged.set(item.itemId, {
        ...existing,
        reasons: existing.reasons.includes(reason) ? existing.reasons : [...existing.reasons, reason],
        fragment: existing.fragment ?? fragment,
      })
      return
    }
    merged.set(item.itemId, {
      item,
      reasons: [reason],
      fragment,
      fifo: false,
      usedBy: used.get(item.itemId) ?? null,
    })
  }
  raw.proposal.forEach((item) => add(item, 'proposal'))
  raw.printed.forEach(({ item, fragment }) => add(item, 'printed', fragment))
  raw.exact.forEach((item) => add(item, item.currency === txnCurrency ? 'exact' : 'converted'))
  raw.party.forEach((item) => add(item, 'party'))
  return merged
}

const invoiceDate = (item: Item) => item.issueDate || item.dueDate

/** FIFO rule: same party and same amount with no other clue settles the oldest open item. */
function markFifo(candidates: Candidate[]): Candidate[] {
  const hasPrinted = candidates.some((c) => c.reasons.includes('printed'))
  if (hasPrinted) return candidates
  const oldestByGroup = new Map<string, Candidate>()
  const sizes = new Map<string, number>()
  for (const candidate of candidates) {
    if (candidate.usedBy || !candidate.item.isOpen) continue
    const key = `${candidate.item.partyId}|${candidate.item.currency}|${candidate.item.openAmount.toFixed(2)}`
    sizes.set(key, (sizes.get(key) ?? 0) + 1)
    const current = oldestByGroup.get(key)
    if (!current || invoiceDate(candidate.item) < invoiceDate(current.item)) {
      oldestByGroup.set(key, candidate)
    }
  }
  const fifoIds = new Set(
    [...oldestByGroup.entries()]
      .filter(([key]) => (sizes.get(key) ?? 0) > 1)
      .map(([, candidate]) => candidate.item.itemId),
  )
  const usedInGroup = new Set(
    candidates
      .filter((c) => c.usedBy)
      .map((c) => `${c.item.partyId}|${c.item.currency}|${c.item.openAmount.toFixed(2)}`),
  )
  return candidates.map((candidate) => {
    const key = `${candidate.item.partyId}|${candidate.item.currency}|${candidate.item.openAmount.toFixed(2)}`
    const nextAfterUsed =
      usedInGroup.has(key) && !candidate.usedBy && oldestByGroup.get(key)?.item.itemId === candidate.item.itemId
    return { ...candidate, fifo: fifoIds.has(candidate.item.itemId) || nextAfterUsed }
  })
}

export function rankCandidates(
  txn: { amount: number; currency: string; date: string },
  raw: RawCandidates,
  used: Map<string, string>,
): Candidate[] {
  const candidates = markFifo([...collect(txn.currency, raw, used).values()])
  return [...candidates].sort(
    (a, b) => score(b) - score(a) || invoiceDate(a.item).localeCompare(invoiceDate(b.item)),
  )
}

export type CandidateGroups = { best: Candidate[]; party: Candidate[]; others: Candidate[] }

const isGoodMatch = ({ reasons }: Candidate) =>
  reasons.some((reason) => ['proposal', 'printed', 'exact', 'search'].includes(reason)) ||
  (reasons.includes('converted') && reasons.includes('party'))

/** The reviewer sees likely matches first; amounts of unrelated parties are almost always noise. */
export function groupCandidates(candidates: Candidate[]): CandidateGroups {
  const best = candidates.filter(isGoodMatch)
  const rest = candidates.filter((candidate) => !isGoodMatch(candidate))
  return {
    best,
    party: rest.filter(({ reasons }) => reasons.includes('party')),
    others: rest.filter(({ reasons }) => !reasons.includes('party')),
  }
}

export type Fit = { fit: 'exact' | 'leaves' | 'over' | 'unknown'; gap: number; equivalent: number | null }

/** What this item would do to the part of the movement still unapplied. */
export function describeFit(
  item: Item,
  remaining: number,
  txn: { currency: string; date: string },
  rates: FxRate[],
): Fit {
  const size = Math.abs(item.openAmount)
  const value = convert(size, item.currency, txn.currency, txn.date, rates)
  if (value === null) return { fit: 'unknown', gap: 0, equivalent: null }
  const equivalent = item.currency === txn.currency ? null : value
  const gap = item.kind === 'CN' ? remaining + value : remaining - value
  if (Math.abs(gap) <= tolerance(remaining)) return { fit: 'exact', gap: 0, equivalent }
  return gap > 0 ? { fit: 'leaves', gap, equivalent } : { fit: 'over', gap: -gap, equivalent }
}
