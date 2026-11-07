import type { RawCandidates } from '../domain/candidates'
import { convert, tolerance } from '../domain/money'
import { extractPrintedTokens, type PrintedToken } from '../domain/printed'
import type { Decision, ExceptionCode, FxRate, Item, ItemKind, ProposalRow, Txn } from '../domain/types'
import { isException } from '../domain/types'
import { lit, numOr, numOrNull, str, strOrNull, type Db, type Priority, type PriorityOf, type Row } from './db'
import type { DatasetMeta } from './ingest'

export type CaseData = {
  txn: Txn
  proposal: ProposalRow[]
  raw: RawCandidates
  tokens: PrintedToken[]
  matchedFragments: string[]
  items: Map<string, Item>
  decision: Decision | null
  usedElsewhere: Map<string, string>
}

const ITEM_COLUMNS =
  'item_id, kind, printed, party_id, party_name, amount, open_amount, currency, due_date, is_open, terms, printed_norm'
const PARTY_ITEM_LIMIT = 60
const EXACT_LIMIT = 30

export function rowToItem(r: Row): Item {
  return {
    itemId: str(r.item_id),
    kind: str(r.kind) as ItemKind,
    printed: str(r.printed),
    partyId: str(r.party_id),
    partyName: str(r.party_name),
    amount: numOr(r.amount, 0),
    openAmount: numOr(r.open_amount, numOr(r.amount, 0)),
    currency: str(r.currency),
    dueDate: str(r.due_date),
    isOpen: Boolean(r.is_open),
    terms: str(r.terms),
  }
}

function rowToTxn(r: Row): Txn {
  return {
    rid: numOr(r.rid, 0),
    id: str(r.id),
    date: str(r.date),
    amount: numOr(r.amount, 0),
    currency: str(r.currency),
    account: str(r.account),
    description: str(r.description),
  }
}

function amountRanges(txn: Txn, currencies: string[], rates: FxRate[]): string[] {
  const target = Math.abs(txn.amount)
  return currencies.flatMap((currency) => {
    const value = convert(target, txn.currency, currency, txn.date, rates)
    if (value === null) return []
    const slack = currency === txn.currency ? 0.01 : tolerance(value)
    const low = Math.round((value - slack) * 100)
    const high = Math.round((value + slack) * 100)
    return [`(currency = ${lit(currency)} AND open_cents BETWEEN ${low} AND ${high})`]
  })
}

function kindsForSign(amount: number): string {
  return amount >= 0 ? "('AR','CN','OTHER')" : "('AP','CN','OTHER')"
}

async function loadItems(db: Db, where: string, priority: PriorityOf, suffix = ''): Promise<Row[]> {
  return db.query(`SELECT ${ITEM_COLUMNS} FROM items WHERE ${where} ${suffix}`, priority)
}

async function loadDecision(db: Db, txn: Txn, priority: PriorityOf): Promise<Decision | null> {
  const rows = await db.query(
    `SELECT item_id, amount, category, exception, source, batch, ts FROM decision_rows WHERE rid = ${txn.rid}`,
    priority,
  )
  if (!rows.length) return null
  const first = rows[0]
  const exception = strOrNull(first.exception)
  return {
    txnId: txn.id,
    allocations: rows.flatMap((r) =>
      r.item_id ? [{ itemId: str(r.item_id), amount: numOr(r.amount, 0) }] : [],
    ),
    category: strOrNull(first.category),
    exception: exception && isException(exception) ? (exception as ExceptionCode) : null,
    source: str(first.source) as Decision['source'],
    at: numOr(first.ts, 0),
    batch: strOrNull(first.batch) ?? undefined,
  }
}

export async function loadCase(
  db: Db,
  meta: DatasetMeta,
  rid: number,
  priority: PriorityOf = 'high',
): Promise<CaseData> {
  const [txnRow] = await db.query(
    `SELECT rid, id, date, amount, currency, account, description FROM txn WHERE rid = ${rid}`,
    priority,
  )
  if (!txnRow) throw new Error(`No existe el movimiento ${rid}`)
  const txn = rowToTxn(txnRow)
  const tokens = extractPrintedTokens(txn.description)

  const propRows = await db.query(
    `SELECT item_id, amount, exception, category, conf, reason FROM prop WHERE rid = ${rid}`,
    priority,
  )
  const proposal: ProposalRow[] = propRows.map((r) => ({
    itemId: strOrNull(r.item_id),
    amount: numOrNull(r.amount),
    exception: strOrNull(r.exception),
    category: strOrNull(r.category),
    confidence: numOrNull(r.conf),
    reason: str(r.reason),
  }))

  const proposalIds = proposal.flatMap((p) => (p.itemId ? [p.itemId] : []))
  const proposalItems = proposalIds.length
    ? await loadItems(db, `item_id IN (${proposalIds.map(lit).join(',')})`, priority)
    : []

  const printedRows = tokens.length
    ? await loadItems(db, `printed_norm IN (${tokens.flatMap((t) => t.variants).map(lit).join(',')})`, priority)
    : []
  const matchedFragments: string[] = []
  const printed = printedRows.map((r) => {
    const token = tokens.find((t) => t.variants.includes(str(r.printed_norm)))
    if (token) matchedFragments.push(token.norm)
    return { item: rowToItem(r), fragment: token?.text ?? str(r.printed) }
  })

  const ranges = amountRanges(txn, meta.currencies, meta.rates)
  const exactRows = ranges.length
    ? await loadItems(
        db,
        `(${ranges.join(' OR ')}) AND is_open AND kind IN ${kindsForSign(txn.amount)}`,
        priority,
        `ORDER BY due_date LIMIT ${EXACT_LIMIT}`,
      )
    : []

  const named = await db.query(
    `SELECT party_id FROM parties WHERE length(name_norm) >= 5 AND contains(${lit(txn.description.toUpperCase())}, name_norm) LIMIT 5`,
    priority,
  )
  const partyIds = new Set<string>([
    ...proposalItems.map((r) => str(r.party_id)),
    ...printedRows.map((r) => str(r.party_id)),
    ...named.map((r) => str(r.party_id)),
  ])
  partyIds.delete('')
  const partyRows = partyIds.size
    ? await loadItems(
        db,
        `party_id IN (${[...partyIds].map(lit).join(',')}) AND is_open`,
        priority,
        `ORDER BY due_date LIMIT ${PARTY_ITEM_LIMIT}`,
      )
    : []

  const raw: RawCandidates = {
    proposal: proposalItems.map(rowToItem),
    printed,
    exact: exactRows.map(rowToItem),
    party: partyRows.map(rowToItem),
  }

  const items = new Map<string, Item>()
  for (const item of [...raw.proposal, ...raw.printed.map((p) => p.item), ...raw.exact, ...raw.party]) {
    items.set(item.itemId, item)
  }

  const ids = [...items.keys()]
  const usedRows = ids.length
    ? await db.query(
        `SELECT item_id, txn_id FROM decision_rows WHERE rid <> ${rid} AND item_id IN (${ids.map(lit).join(',')})`,
        priority,
      )
    : []

  return {
    txn,
    proposal,
    raw,
    tokens,
    matchedFragments,
    items,
    decision: await loadDecision(db, txn, priority),
    usedElsewhere: new Map(usedRows.map((r) => [str(r.item_id), str(r.txn_id)])),
  }
}

export async function searchItems(db: Db, term: string): Promise<Item[]> {
  const trimmed = term.trim()
  if (trimmed.length < 2) return []
  const asNumber = Number(trimmed.replace(/\./g, '').replace(',', '.'))
  const pattern = lit(`%${trimmed.toUpperCase()}%`)
  const norm = trimmed.toUpperCase().replace(/[^A-Z0-9]/g, '')
  const conditions = [
    `upper(item_id) LIKE ${pattern}`,
    `upper(party_name) LIKE ${pattern}`,
    `upper(party_id) LIKE ${pattern}`,
    ...(norm ? [`printed_norm LIKE ${lit(`%${norm}%`)}`] : []),
    ...(Number.isFinite(asNumber) && /\d/.test(trimmed)
      ? [`open_cents = ${Math.round(Math.abs(asNumber) * 100)}`]
      : []),
  ]
  const rows = await loadItems(db, `(${conditions.join(' OR ')})`, 'high', 'ORDER BY is_open DESC, due_date LIMIT 30')
  return rows.map(rowToItem)
}

export type PdfHit = { file: string; page: number; snippet: string }

const SNIPPET_RADIUS = 70

function makeSnippet(text: string, needle: string): string {
  const flat = text.replace(/\s+/g, ' ')
  const at = flat.toUpperCase().indexOf(needle.toUpperCase())
  if (at < 0) return flat.slice(0, SNIPPET_RADIUS * 2)
  const start = Math.max(0, at - SNIPPET_RADIUS)
  return `${start ? '…' : ''}${flat.slice(start, at + needle.length + SNIPPET_RADIUS)}…`
}

export async function searchPdfs(db: Db, terms: string[], priority: Priority = 'low'): Promise<PdfHit[]> {
  const clean = terms.filter((t) => t.trim().length >= 3)
  if (!clean.length) return []
  const norms = clean.map((t) => t.toUpperCase().replace(/[^A-Z0-9]/g, '')).filter(Boolean)
  if (!norms.length) return []
  const rows = await db.query(
    `SELECT file, page, text FROM pdf_text WHERE ${norms.map((n) => `contains(text_norm, ${lit(n)})`).join(' OR ')} LIMIT 12`,
    priority,
  )
  return rows.map((r) => {
    const text = str(r.text)
    const needle = clean.find((t) => text.toUpperCase().includes(t.toUpperCase())) ?? clean[0]
    return { file: str(r.file), page: numOr(r.page, 1), snippet: makeSnippet(text, needle) }
  })
}
