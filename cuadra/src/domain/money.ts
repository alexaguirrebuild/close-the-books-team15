import type { FxRate } from './types'

const MIN_TOLERANCE = 0.01
const RELATIVE_TOLERANCE = 0.005

export function tolerance(amount: number): number {
  return Math.max(MIN_TOLERANCE, RELATIVE_TOLERANCE * Math.abs(amount))
}

export function isBalanced(expected: number, actual: number): boolean {
  return Math.abs(expected - actual) <= tolerance(expected) + 1e-9
}

const PIVOT = 'EUR'

type RateBook = Map<string, FxRate[]>

/** convert() runs in loops over a party's items: rates are looked up by pair and date, not scanned. */
const BOOKS = new WeakMap<FxRate[], RateBook>()

function bookOf(rates: FxRate[]): RateBook {
  const cached = BOOKS.get(rates)
  if (cached) return cached
  const grouped = new Map<string, FxRate[]>()
  for (const rate of rates) {
    const key = `${rate.base}|${rate.quote}`
    const list = grouped.get(key)
    if (list) list.push(rate)
    else grouped.set(key, [rate])
  }
  const book: RateBook = new Map(
    [...grouped].map(([key, list]) => [key, [...list].sort((a, b) => a.date.localeCompare(b.date))]),
  )
  BOOKS.set(rates, book)
  return book
}

/** Last rate on or before the date; weekends and holidays have none of their own. */
function rateAt(list: FxRate[], date: string): number {
  let low = 0
  let high = list.length - 1
  let found = 0
  while (low <= high) {
    const middle = (low + high) >> 1
    if (list[middle].date <= date) {
      found = middle
      low = middle + 1
    } else {
      high = middle - 1
    }
  }
  return list[found].rate
}

function pairRate(book: RateBook, from: string, to: string, date: string): number | null {
  const direct = book.get(`${from}|${to}`)
  if (direct) return rateAt(direct, date)
  const inverse = book.get(`${to}|${from}`)
  return inverse ? 1 / rateAt(inverse, date) : null
}

export function findRate(rates: FxRate[], from: string, to: string, date: string): number | null {
  if (from === to) return 1
  const book = bookOf(rates)
  const known = pairRate(book, from, to, date)
  if (known !== null) return known
  const toPivot = pairRate(book, from, PIVOT, date)
  const fromPivot = pairRate(book, PIVOT, to, date)
  return toPivot === null || fromPivot === null ? null : toPivot * fromPivot
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

const THOUSANDS_ONLY = /^\d{1,3}(\.\d{3})+$/

/** Reads an amount the way people type it here: 1.234,56, 1234,5 or 1234.56. */
export function parseAmount(text: string): number | null {
  const clean = text.trim().replace(/\s/g, '')
  if (!clean) return null
  const lastComma = clean.lastIndexOf(',')
  const lastDot = clean.lastIndexOf('.')
  let plain = clean
  if (lastComma >= 0 && lastDot >= 0) {
    plain = lastComma > lastDot ? clean.replace(/\./g, '').replace(',', '.') : clean.replace(/,/g, '')
  } else if (lastComma >= 0) {
    plain = clean.replace(',', '.')
  } else if (THOUSANDS_ONLY.test(clean)) {
    plain = clean.replace(/\./g, '')
  }
  const value = Number(plain)
  return Number.isFinite(value) ? value : null
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
