import { ident, lit, numOr, numOrNull, sqlNum, str, strOrNull, type Db } from './db'

export type QueueSort = 'amount' | 'confidence_low' | 'confidence_high'
export type CaseType = 'sin_propuesta' | 'uno_a_uno' | 'uno_a_n' | 'excepcion' | 'multidivisa' | 'categoria'

export const CASE_TYPE_LABEL: Record<CaseType, string> = {
  sin_propuesta: 'Sin propuesta',
  uno_a_uno: '1:1',
  uno_a_n: '1:N',
  excepcion: 'Con excepción',
  multidivisa: 'Multidivisa',
  categoria: 'Sin partida',
}

export type QueueStatus = 'pending' | 'decided' | 'all'

export const STATUS_LABEL: Record<QueueStatus, string> = {
  pending: 'Pendientes',
  decided: 'Decididos',
  all: 'Todos',
}

export type QueueFilters = {
  status: QueueStatus
  currency: string
  account: string
  party: string
  caseType: CaseType | ''
  sort: QueueSort
}

export const DEFAULT_FILTERS: QueueFilters = {
  status: 'pending',
  currency: '',
  account: '',
  party: '',
  caseType: '',
  sort: 'amount',
}

export type QueueRow = {
  pos: number
  rid: number
  id: string
  date: string
  amount: number
  currency: string
  account: string
  description: string
  conf: number | null
  partyName: string | null
  caseType: CaseType
  exception: string | null
  resolved: boolean
}

const ORDER: Record<QueueSort, string> = {
  amount: 'abs_amount DESC, conf ASC NULLS FIRST, rid',
  confidence_low: 'conf ASC NULLS FIRST, abs_amount DESC, rid',
  confidence_high: 'conf DESC NULLS LAST, abs_amount DESC, rid',
}

const TEXT_COLUMNS = ['t.party_name', 't.party_id', 't.description', 't.id']
const DECIDED_EXISTS = 'EXISTS (SELECT 1 FROM decision_rows d WHERE d.rid = t.rid)'
const STATUS_CLAUSE: Record<QueueStatus, string> = {
  pending: `AND NOT ${DECIDED_EXISTS}`,
  decided: `AND ${DECIDED_EXISTS}`,
  all: '',
}

export function filterClause(filters: QueueFilters): string {
  const conditions = [
    filters.currency && `t.currency = ${lit(filters.currency)}`,
    filters.account && `t.account = ${lit(filters.account)}`,
    filters.caseType && `t.case_type = ${lit(filters.caseType)}`,
    filters.party &&
      `(${TEXT_COLUMNS.map((column) => `upper(${column}) LIKE ${lit(`%${filters.party.toUpperCase()}%`)}`).join(' OR ')})`,
  ].filter(Boolean)
  return conditions.length ? conditions.join(' AND ') : 'true'
}

/** Materialises the pending queue so paging stays fast and positions stay stable while reviewing. */
export async function buildQueue(db: Db, filters: QueueFilters): Promise<number> {
  await db.query(
    `CREATE OR REPLACE TABLE q AS
     SELECT CAST(row_number() OVER (ORDER BY ${ORDER[filters.sort]}) AS INTEGER) AS pos, t.rid
     FROM txn_q t
     WHERE ${filterClause(filters)} ${STATUS_CLAUSE[filters.status]}`,
  )
  const [row] = await db.query('SELECT count(*) AS n FROM q')
  return numOr(row?.n, 0)
}

export async function queuePage(db: Db, offset: number, limit: number): Promise<QueueRow[]> {
  const rows = await db.query(
    `SELECT q.pos, t.rid, t.id, t.date, t.amount, t.currency, t.account, t.description, t.conf,
            t.party_name, t.case_type, t.exception,
            EXISTS (SELECT 1 FROM decision_rows d WHERE d.rid = t.rid) AS resolved
     FROM q JOIN txn_q t ON t.rid = q.rid
     WHERE q.pos > ${offset} AND q.pos <= ${offset + limit}
     ORDER BY q.pos`,
  )
  return rows.map((r) => ({
    pos: numOr(r.pos, 0),
    rid: numOr(r.rid, 0),
    id: str(r.id),
    date: str(r.date),
    amount: numOr(r.amount, 0),
    currency: str(r.currency),
    account: str(r.account),
    description: str(r.description),
    conf: numOrNull(r.conf),
    partyName: strOrNull(r.party_name),
    caseType: str(r.case_type) as CaseType,
    exception: strOrNull(r.exception),
    resolved: Boolean(r.resolved),
  }))
}

export type QueueStep = { pos: number; rid: number }

export async function queueWindow(db: Db, fromPos: number, limit: number): Promise<QueueStep[]> {
  const rows = await db.query(
    `SELECT pos, rid FROM q WHERE pos >= ${fromPos} AND pos < ${fromPos + limit} ORDER BY pos`,
    'high',
  )
  return rows.map((r) => ({ pos: numOr(r.pos, 0), rid: numOr(r.rid, 0) }))
}

export async function queuePosition(db: Db, rid: number): Promise<number | null> {
  const [row] = await db.query(`SELECT pos FROM q WHERE rid = ${rid} LIMIT 1`)
  return numOrNull(row?.pos)
}

export async function findTxn(db: Db, term: string): Promise<number | null> {
  const [row] = await db.query(`SELECT rid FROM txn WHERE id = ${lit(term.trim())} LIMIT 1`)
  return numOrNull(row?.rid)
}

export async function bulkEligibleCount(db: Db, minConf: number): Promise<number> {
  const [row] = await db.query(
    `SELECT count(*) AS n FROM q JOIN txn_q t ON t.rid = q.rid
     WHERE t.conf >= ${sqlNum(minConf)}
       AND NOT EXISTS (SELECT 1 FROM decision_rows d WHERE d.rid = q.rid)`,
  )
  return numOr(row?.n, 0)
}

export async function filterOptions(db: Db, column: 'currency' | 'account'): Promise<string[]> {
  const rows = await db.query(`SELECT DISTINCT ${ident(column)} AS v FROM txn ORDER BY 1`)
  return rows.map((r) => str(r.v))
}
