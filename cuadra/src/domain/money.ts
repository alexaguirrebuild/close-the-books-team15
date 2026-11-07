import type { FxRate } from './types'

const MIN_TOLERANCE = 0.01
const RELATIVE_TOLERANCE = 0.005

export function tolerance(amount: number): number {
  return Math.max(MIN_TOLERANCE, RELATIVE_TOLERANCE * Math.abs(amount))
}

export function isBalanced(expected: number, actual: number): boolean {
  return Math.abs(expected - actual) <= tolerance(expected) + 1e-9
}

export function formatExportAmount(amount: number): string {
  const fixed = amount.toFixed(2)
  return fixed === '-0.00' ? '0.00' : fixed
}

export function findRate(rates: FxRate[], from: string, to: string, date: string): number | null {
  if (from === to) return 1
  const direct = rates.filter((r) => r.base === from && r.quote === to)
  const inverse = rates.filter((r) => r.base === to && r.quote === from)
  const pick = (list: FxRate[]) => {
    const sorted = [...list].sort((a, b) => a.date.localeCompare(b.date))
    return sorted.filter((r) => r.date <= date).at(-1) ?? sorted[0]
  }
  const directRate = pick(direct)
  if (directRate) return directRate.rate
  const inverseRate = pick(inverse)
  return inverseRate ? 1 / inverseRate.rate : null
}

export function convert(
  amount: number,
  from: string,
  to: string,
  date: string,
  rates: FxRate[],
): number | null {
  const rate = findRate(rates, from, to, date)
  return rate === null ? null : amount * rate
}

export function formatMoney(amount: number, currency?: string): string {
  const formatted = new Intl.NumberFormat('es-ES', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    useGrouping: 'always',
  }).format(amount)
  return currency ? `${formatted} ${currency}` : formatted
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat('es-ES', { useGrouping: 'always' }).format(value)
}

export function formatPercent(ratio: number, digits = 1): string {
  return `${new Intl.NumberFormat('es-ES', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(ratio * 100)} %`
}
