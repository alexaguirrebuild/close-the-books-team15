const TOKEN_PATTERN = /[A-Za-z0-9][A-Za-z0-9\-/.]*[A-Za-z0-9]/g
const MIN_TOKEN_LENGTH = 4

export type PrintedToken = {
  text: string
  start: number
  end: number
  norm: string
  /** Ways the number may be printed on the item, since banks often split it with spaces. */
  variants: string[]
}
export type Range = { start: number; end: number }
export type Segment = { text: string; hit: boolean }

export function normalizePrinted(value: string): string {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, '')
}

const PREFIX_BEFORE = /([A-Za-z]{1,4})[\s\-/.]*$/

function variantsOf(concept: string, norm: string, start: number, next?: string): string[] {
  const prefix = concept.slice(0, start).match(PREFIX_BEFORE)?.[1]?.toUpperCase()
  const joined = next ? [`${norm}${next}`] : []
  const prefixed = prefix ? [`${prefix}${norm}`, ...joined.map((j) => `${prefix}${j}`)] : []
  return [norm, ...prefixed, ...joined]
}

export function extractPrintedTokens(concept: string): PrintedToken[] {
  const found = [...concept.matchAll(TOKEN_PATTERN)]
    .map((match) => ({
      text: match[0],
      start: match.index,
      end: match.index + match[0].length,
      norm: normalizePrinted(match[0]),
    }))
    .filter((token) => /\d/.test(token.norm) && token.norm.length >= MIN_TOKEN_LENGTH)
  return found.map((token, index) => {
    const following = found[index + 1]
    const adjacent = following && /^\s+$/.test(concept.slice(token.end, following.start))
    return { ...token, variants: variantsOf(concept, token.norm, token.start, adjacent ? following.norm : undefined) }
  })
}

export function highlightSegments(concept: string, ranges: Range[]): Segment[] {
  const merged = [...ranges]
    .sort((a, b) => a.start - b.start)
    .reduce<Range[]>((acc, range) => {
      const last = acc.at(-1)
      if (last && range.start <= last.end) {
        return [...acc.slice(0, -1), { start: last.start, end: Math.max(last.end, range.end) }]
      }
      return [...acc, range]
    }, [])

  const segments: Segment[] = []
  let cursor = 0
  for (const range of merged) {
    if (range.start > cursor) segments.push({ text: concept.slice(cursor, range.start), hit: false })
    segments.push({ text: concept.slice(range.start, range.end), hit: true })
    cursor = range.end
  }
  if (cursor < concept.length) segments.push({ text: concept.slice(cursor), hit: false })
  return segments
}
