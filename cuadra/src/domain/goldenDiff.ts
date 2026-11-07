import { tolerance } from './money'
import type { Allocation, ExceptionCode } from './types'

export type DiagnosisKind = 'ok' | 'wrong_item' | 'wrong_amount' | 'wrong_exception' | 'wrong_category' | 'no_row'

export const DIAGNOSIS_LABEL: Record<DiagnosisKind, string> = {
  ok: 'Coincide con el golden',
  wrong_item: 'Partida errónea',
  wrong_amount: 'Importe erróneo',
  wrong_exception: 'Excepción errónea',
  wrong_category: 'Categoría errónea',
  no_row: 'Sin decisión',
}

export type GoldenRow = {
  item: string | null
  amount: number | null
  exception: string | null
  category: string | null
  segment: string
  difficulty: number | null
}

export type DiffState = 'ok' | 'missing' | 'extra' | 'amount' | 'exception'

export type DiffRow = {
  item: string
  state: DiffState
  myAmount: number | null
  goldAmount: number | null
  myException: string | null
  goldException: string | null
}

export type GoldenDiff = { ok: boolean; kind: DiagnosisKind; text: string; rows: DiffRow[]; segment: string | null }

type Line = { rows: Allocation[]; category: string | null; exception: ExceptionCode | null }

const matches = (mine: number, gold: number) => Math.abs(mine - gold) <= tolerance(gold) + 1e-9

function compareRows(rows: Allocation[], golden: GoldenRow[]): DiffRow[] {
  const goldItems = golden.filter((g) => g.item)
  const fromGold = goldItems.map((g): DiffRow => {
    const mine = rows.find((row) => row.itemId === g.item)
    const base = { item: g.item as string, goldAmount: g.amount, goldException: g.exception, myAmount: mine?.amount ?? null, myException: mine?.exception ?? null }
    if (!mine) return { ...base, state: 'missing' }
    if (!matches(mine.amount, g.amount ?? 0)) return { ...base, state: 'amount' }
    if ((mine.exception ?? null) !== (g.exception ?? null)) return { ...base, state: 'exception' }
    return { ...base, state: 'ok' }
  })
  const extras = rows
    .filter((row) => !goldItems.some((g) => g.item === row.itemId))
    .map((row): DiffRow => ({ item: row.itemId, state: 'extra', myAmount: row.amount, goldAmount: null, myException: row.exception, goldException: null }))
  return [...fromGold, ...extras]
}

/** Same rules as the scorer, row by row, against what is on screen right now. */
export function diffAgainstGolden(line: Line, golden: GoldenRow[]): GoldenDiff {
  const segment = golden[0]?.segment ?? null
  const result = (kind: DiagnosisKind, rows: DiffRow[], text = DIAGNOSIS_LABEL[kind]): GoldenDiff => ({ ok: kind === 'ok', kind, text, rows, segment })
  const noItem = golden.find((g) => !g.item)
  const decidedNoItem = !line.rows.length && Boolean(line.category || line.exception)

  if (!line.rows.length && !decidedNoItem) return result('no_row', compareRows([], golden))
  if (noItem) {
    if (line.rows.length) return result('wrong_item', compareRows(line.rows, golden), `Partida errónea: el golden dice ${noItem.category ?? 'sin partida'}`)
    if ((line.category ?? null) !== (noItem.category ?? null)) {
      return result('wrong_category', [], `Categoría errónea: el golden dice ${noItem.category ?? 'ninguna'}`)
    }
    if ((line.exception ?? null) !== (noItem.exception ?? null)) {
      return result('wrong_exception', [], `Excepción errónea: el golden dice ${noItem.exception ?? 'ninguna'}`)
    }
    return result('ok', [])
  }
  const rows = compareRows(line.rows, golden)
  if (rows.some((row) => row.state === 'missing' || row.state === 'extra')) return result('wrong_item', rows)
  if (rows.some((row) => row.state === 'amount')) return result('wrong_amount', rows)
  if (rows.some((row) => row.state === 'exception')) return result('wrong_exception', rows)
  return result('ok', rows)
}
