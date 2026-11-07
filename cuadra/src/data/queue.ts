import { lit, numOr, numOrNull, sqlNum, str, strOrNull, type Db } from './db'

export type QueueSort = 'auto' | 'amount' | 'confidence_low' | 'confidence_high'
export type CaseType = 'sin_propuesta' | 'uno_a_uno' | 'uno_a_n' | 'excepcion' | 'multidivisa' | 'categoria'
export type QueueView = 'priority' | 'ready' | 'exceptions' | 'no_proposal' | 'done' | 'errors' | 'all'

export const CASE_TYPE_LABEL: Record<CaseType, string> = {
  sin_propuesta: 'Sin propuesta',
  uno_a_uno: 'Una partida',
  uno_a_n: 'Varias partidas',
  excepcion: 'Con excepción',
  multidivisa: 'Otra divisa',
  categoria: 'Sin partida',
}

export const VIEW_LABEL: Record<QueueView, string> = {
  priority: 'Prioridad',
  ready: 'Listas',
  exceptions: 'Con excepción',
  no_proposal: 'Sin propuesta',
  done: 'Hechas',
  errors: 'Errores',
  all: 'Todas',
}

export const VIEW_HINT: Record<QueueView, string> = {
  priority: 'Primero lo que más puede subir la puntuación.',
  ready: 'Propuestas con confianza alta, para confirmar en lote.',
  exceptions: 'La propuesta trae una excepción: compruébala.',
  no_proposal: 'No hay propuesta: hay que decidir a mano.',
  done: 'Lo ya decidido, por si hay que corregirlo.',
  errors: 'La propuesta no coincide con el golden.',
  all: 'Todos los movimientos de la fase.',
}

export const VIEW_ORDER_IN_TABS: QueueView[] = ['priority', 'ready', 'exceptions', 'no_proposal', 'errors', 'done', 'all']

type ViewSupport = { hasGolden: boolean; hasConfidence: boolean }

/** Errors need a golden, and a list ready to confirm needs something that says how sure a proposal is. */
export function availableViews(support: ViewSupport | null): QueueView[] {
  return VIEW_ORDER_IN_TABS.filter((view) => (view !== 'errors' || support?.hasGolden) && (view !== 'ready' || support?.hasConfidence))
}

const PRIORITY_HINT_WITHOUT_CONFIDENCE = 'El motor no da confianza: primero las líneas con más partidas y excepciones raras.'

/** The line that explains a view. Without confidence, the priority view cannot promise to find the doubtful cases. */
export function viewHint(view: QueueView, hasConfidence: boolean): string {
  return view === 'priority' && !hasConfidence ? PRIORITY_HINT_WITHOUT_CONFIDENCE : VIEW_HINT[view]
}

export const DEFAULT_READY_THRESHOLD = 0.95

export type QueueFilters = {
  view: QueueView
  readyThreshold: number
  currency: string
  account: string
  entity: string
  party: string
  caseType: CaseType | ''
  sort: QueueSort
  /** Restricts the Errors view to one segment of the golden. */
  segment: string
}

export const DEFAULT_FILTERS: QueueFilters = {
  view: 'priority',
  readyThreshold: DEFAULT_READY_THRESHOLD,
  currency: '',
  account: '',
  entity: '',
  party: '',
  caseType: '',
  sort: 'auto',
  segment: '',
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
  counterparty: string | null
  caseType: CaseType
  exception: string | null
  resolved: boolean
}

/**
 * The score counts pairs, lines and rare exceptions, never money, so that is what ranks the work:
 * how unsure the proposal is, times what the line is worth (POLICIES weights 0.5, 0.3 and 0.2).
 */
const PAIRS_THAT_COUNT_IN_FULL = 50
const IMPACT = `(1 - coalesce(conf, 0)) * (0.3 + 0.5 * least(coalesce(n_items, 0), ${PAIRS_THAT_COUNT_IN_FULL}) / ${PAIRS_THAT_COUNT_IN_FULL}.0 + 0.2 * coalesce(rarity, 0))`

const ORDER: Record<Exclude<QueueSort, 'auto'>, string> = {
  amount: 'abs_eur DESC, conf ASC NULLS FIRST, rid',
  confidence_low: 'conf ASC NULLS FIRST, abs_eur DESC, rid',
  confidence_high: 'conf DESC NULLS LAST, abs_eur DESC, rid',
}

const VIEW_ORDER: Record<QueueView, string> = {
  priority: `${IMPACT} DESC, abs_eur DESC, rid`,
  ready: ORDER.confidence_low,
  exceptions: `coalesce(rarity, 0) DESC, ${ORDER.confidence_low}`,
  no_proposal: ORDER.amount,
  done: 'rid DESC',
  errors: ORDER.confidence_high,
  all: ORDER.amount,
}

const orderOf = ({ sort, view }: QueueFilters) => (sort === 'auto' ? VIEW_ORDER[view] : ORDER[sort])

const TEXT_COLUMNS = ['t.party_name', 't.party_id', 't.description', 't.counterparty', 't.id']
const DECIDED = 'EXISTS (SELECT 1 FROM decision_rows d WHERE d.rid = t.rid)'
const PENDING = `NOT ${DECIDED}`

function viewCondition(view: QueueView, readyThreshold: number): string {
  const ready = sqlNum(readyThreshold)
  const conditions: Record<QueueView, string> = {
    priority: `${PENDING} AND (t.conf IS NULL OR t.conf < ${ready})`,
    ready: `${PENDING} AND t.conf >= ${ready}`,
    exceptions: `${PENDING} AND t.exception IS NOT NULL`,
    no_proposal: `${PENDING} AND t.case_type = 'sin_propuesta'`,
    done: DECIDED,
    errors: 'EXISTS (SELECT 1 FROM diag g WHERE g.rid = t.rid AND g.kind <> \'ok\')',
    all: 'true',
  }
  return conditions[view]
}

export function filterClause(filters: QueueFilters): string {
  const conditions = [
    filters.currency && `t.currency = ${lit(filters.currency)}`,
    filters.account && `t.account = ${lit(filters.account)}`,
    filters.entity && `t.entity = ${lit(filters.entity)}`,
    filters.segment && `EXISTS (SELECT 1 FROM diag g WHERE g.rid = t.rid AND g.segment = ${lit(filters.segment)})`,
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
     SELECT CAST(row_number() OVER (ORDER BY ${orderOf(filters)}) AS INTEGER) AS pos, t.rid
     FROM txn_q t
     WHERE ${filterClause(filters)} AND ${viewCondition(filters.view, filters.readyThreshold)}`,
  )
  const [row] = await db.query('SELECT count(*) AS n FROM q')
  return numOr(row?.n, 0)
}

export async function queuePage(db: Db, offset: number, limit: number): Promise<QueueRow[]> {
  const rows = await db.query(
    `SELECT q.pos, t.rid, t.id, t.date, t.amount, t.currency, t.account, t.description, t.conf,
            t.party_name, t.counterparty, t.case_type, t.exception,
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
    counterparty: strOrNull(r.counterparty),
    caseType: str(r.case_type) as CaseType,
    exception: strOrNull(r.exception),
    resolved: Boolean(r.resolved),
  }))
}

export type ViewCounts = Record<QueueView, number> & { totalAll: number; doneAll: number }

const COUNTED_VIEWS: QueueView[] = ['priority', 'ready', 'exceptions', 'no_proposal', 'done', 'all']

/** Every count of the views in one scan, so the tabs never disagree with each other. */
export async function viewCounts(db: Db, filters: QueueFilters, hasDiagnosis = false): Promise<ViewCounts> {
  const views = hasDiagnosis ? [...COUNTED_VIEWS, 'errors' as const] : COUNTED_VIEWS
  const filter = filterClause(filters)
  const columns = views.map(
    (view) => `count(*) FILTER (WHERE (${filter}) AND ${viewCondition(view, filters.readyThreshold)}) AS ${view}`,
  )
  const [row] = await db.query(
    `SELECT ${columns.join(', ')}, count(*) AS total_all, count(*) FILTER (WHERE ${DECIDED}) AS done_all FROM txn_q t`,
    'low',
  )
  return {
    priority: numOr(row?.priority, 0),
    ready: numOr(row?.ready, 0),
    exceptions: numOr(row?.exceptions, 0),
    no_proposal: numOr(row?.no_proposal, 0),
    done: numOr(row?.done, 0),
    errors: numOr(row?.errors, 0),
    all: numOr(row?.all, 0),
    totalAll: numOr(row?.total_all, 0),
    doneAll: numOr(row?.done_all, 0),
  }
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

