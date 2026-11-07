import { tolerance } from './money'

export type ComboInput = { id: string; value: number }
export type Combo = { ids: string[]; total: number }

const DEFAULTS = { maxSize: 4, maxItems: 40, limit: 5, stepBudget: 150_000 }
const CANDIDATES_PER_RESULT = 8

/**
 * Subset search for "several items of the same party add up to the movement".
 * Items arrive oldest first, so ties resolve to the oldest ones (FIFO).
 * Values are searched in cents to avoid float drift, and the step budget keeps
 * the keyboard responsive on parties with many open items.
 */
export function findCombos(
  items: ComboInput[],
  target: number,
  options: Partial<typeof DEFAULTS> = {},
): Combo[] {
  const { maxSize, maxItems, limit, stepBudget } = { ...DEFAULTS, ...options }
  const pool = items.slice(0, maxItems).map((i) => ({ id: i.id, cents: Math.round(i.value * 100) }))
  const targetCents = Math.round(target * 100)
  const toleranceCents = Math.round(tolerance(target) * 100)
  const found: Combo[] = []
  const maxFound = limit * CANDIDATES_PER_RESULT
  let steps = 0

  const search = (size: number, start: number, sum: number, chosen: number[]): void => {
    if (found.length >= maxFound || steps > stepBudget) return
    if (chosen.length === size) {
      if (Math.abs(sum - targetCents) <= toleranceCents) {
        found.push({ ids: chosen.map((i) => pool[i].id), total: sum / 100 })
      }
      return
    }
    for (let i = start; i < pool.length; i += 1) {
      steps += 1
      search(size, i + 1, sum + pool[i].cents, [...chosen, i])
      if (found.length >= maxFound || steps > stepBudget) return
    }
  }

  for (let size = 2; size <= maxSize; size += 1) search(size, 0, 0, [])
  const gap = (combo: Combo) => Math.abs(combo.total - target)
  return [...found]
    .sort((a, b) => a.ids.length - b.ids.length || gap(a) - gap(b))
    .slice(0, limit)
}
