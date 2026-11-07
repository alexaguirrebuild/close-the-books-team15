export type Row = Record<string, unknown>
export type Priority = 'high' | 'low'
/** A function lets a queued prefetch jump ahead once its movement becomes the current one. */
export type PriorityOf = Priority | (() => Priority)

/** What the data layer needs from DuckDB; the browser and the tests each provide one. */
export interface Db {
  query(sql: string, priority?: PriorityOf): Promise<Row[]>
  registerFile(name: string, file: File): Promise<void>
  registerText(name: string, text: string): Promise<void>
  readFile(name: string): Promise<Uint8Array>
  dropFile(name: string): Promise<void>
}

export function lit(value: string | null | undefined): string {
  if (value === null || value === undefined) return 'NULL'
  return `'${value.replace(/'/g, "''")}'`
}

export function ident(name: string): string {
  return `"${name.replace(/"/g, '""')}"`
}

export function sqlNum(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'NULL'
  return String(value)
}

export function normalizeValue(value: unknown): unknown {
  if (typeof value === 'bigint') return Number(value)
  return value
}

export function normalizeRow(row: Row): Row {
  return Object.fromEntries(Object.entries(row).map(([key, value]) => [key, normalizeValue(value)]))
}

export const str = (value: unknown): string => (value === null || value === undefined ? '' : String(value))
export const strOrNull = (value: unknown): string | null =>
  value === null || value === undefined || value === '' ? null : String(value)
export const numOr = (value: unknown, fallback: number): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback
export const numOrNull = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

let tempFileCounter = 0

/** Runs a COPY into a uniquely named virtual file and returns its bytes. */
export async function copyToBytes(db: Db, select: string): Promise<Uint8Array> {
  tempFileCounter += 1
  const file = `out_${Date.now()}_${tempFileCounter}.csv`
  await db.query(`COPY (${select}) TO '${file}' (HEADER, DELIMITER ',', FORMAT CSV)`)
  const bytes = await db.readFile(file)
  await db.dropFile(file)
  return bytes
}
