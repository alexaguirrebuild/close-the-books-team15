import type { RawCandidates } from '../domain/candidates'
import { convert, parseAmount, tolerance } from '../domain/money'
import { extractPrintedTokens, type PrintedToken } from '../domain/printed'
import type { Decision, ExceptionCode, FxRate, Item, ItemKind, ProposalRow, Txn } from '../domain/types'
import { isException } from '../domain/types'
import { lit, numOr, numOrNull, str, strOrNull, type Db, type Priority, type PriorityOf, type Row } from './db'
import { goldenRowsFor } from './diagnosis'
import type { DatasetMeta } from './ingest'
import type { GoldenRow } from '../domain/goldenDiff'

export type CaseData = {
  txn: Txn
  proposal: ProposalRow[]
  raw: RawCandidates
  tokens: PrintedToken[]
  matchedFragments: string[]
  items: Map<string, Item>
  decision: Decision | null
  usedElsewhere: Map<string, string>
  /** Only when the dataset has labels: TRAIN and the sample, never LIVE or FINAL. */
  golden: GoldenRow[]
}

const ITEM_COLUMNS =
  'item_id, kind, printed, party_id, party_name, amount, open_amount, currency, due_date, issue_date, terms, po, printed_norm'
const TXN_COLUMNS =
  'rid, id, date, amount, currency, account, description, entity, bank, counterparty, counterparty_account, end_to_end, remittance, ref1, ref2, orig_amount, orig_currency'
const PARTY_ITEM_LIMIT = 100
const EXACT_LIMIT = 30
const PARTIES_PER_CASE = 5
const MIN_PARTY_KEY = 5

/** A movement cannot settle an item issued after it: in the labels this never happens. */
function rowToItem(r: Row, movementDate = ''): Item {
  const issueDate = str(r.issue_date)
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
    issueDate,
    isOpen: !movementDate || !issueDate || issueDate <= movementDate,
    terms: str(r.terms),
    po: str(r.po),
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
    entity: str(r.entity),
    bank: str(r.bank),
    counterparty: str(r.counterparty),
    counterpartyAccount: str(r.counterparty_account),
    endToEnd: str(r.end_to_end),
    remittance: str(r.remittance),
    ref1: str(r.ref1),
    ref2: str(r.ref2),
    origAmount: numOrNull(r.orig_amount),
    origCurrency: str(r.orig_currency),
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

const validException = (value: string | null): ExceptionCode | null => (value && isException(value) ? value : null)

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
      r.item_id ? [{ itemId: str(r.item_id), amount: numOr(r.amount, 0), exception: validException(strOrNull(r.exception)) }] : [],
    ),
    category: strOrNull(first.category),
    exception: first.item_id ? null : validException(exception),
    source: str(first.source) as Decision['source'],
    at: numOr(first.ts, 0),
    batch: strOrNull(first.batch) ?? undefined,
  }
}

const issuedBy = (txn: Txn) => `(issue_date IS NULL OR issue_date <= ${lit(txn.date)})`

/** Bank text, name of the payer and its account: whatever the bank line gives away about who it is. */
async function findParties(db: Db, txn: Txn, priority: PriorityOf): Promise<Row[]> {
  const text = lit([txn.description, txn.counterparty, txn.remittance].join(' '))
  const byAccount = txn.counterpartyAccount ? `iban = ${lit(txn.counterpartyAccount)} OR` : ''
  return db.query(
    `SELECT party_id FROM parties
     WHERE ${byAccount} (length(name_key) >= ${MIN_PARTY_KEY} AND contains(pnorm(${text}), name_key))
        OR (length(trade_key) >= ${MIN_PARTY_KEY} AND contains(pnorm(${text}), trade_key))
     ORDER BY entity = ${lit(txn.entity)} DESC NULLS LAST
     LIMIT ${PARTIES_PER_CASE}`,
    priority,
  )
}

export async function loadCase(
  db: Db,
  meta: DatasetMeta,
  rid: number,
  priority: PriorityOf = 'high',
): Promise<CaseData> {
  const [txnRow] = await db.query(
    `SELECT ${TXN_COLUMNS} FROM txn WHERE rid = ${rid}`,
    priority,
  )
  if (!txnRow) throw new Error(`No existe el movimiento ${rid}`)
  const txn = rowToTxn(txnRow)
  const tokens = extractPrintedTokens(txn.description)
  const toItem = (r: Row) => rowToItem(r, txn.date)

  const propRows = await db.query(
    `SELECT item_id, amount, exception, category, conf, reason, source, advice_id, line_no FROM prop WHERE rid = ${rid}`,
    priority,
  )
  const proposal: ProposalRow[] = propRows.map((r) => ({
    itemId: strOrNull(r.item_id),
    amount: numOrNull(r.amount),
    exception: strOrNull(r.exception),
    category: strOrNull(r.category),
    confidence: numOrNull(r.conf),
    reason: str(r.reason),
    source: str(r.source),
    adviceId: strOrNull(r.advice_id),
    lineNo: strOrNull(r.line_no),
  }))

  const proposalIds = proposal.flatMap((p) => (p.itemId ? [p.itemId] : []))
  const proposalItems = proposalIds.length
    ? await loadItems(db, `item_id IN (${proposalIds.map(lit).join(',')})`, priority)
    : []

  const printedRows = tokens.length
    ? await loadItems(db, `printed_norm IN (${tokens.flatMap((t) => t.variants).map(lit).join(',')})`, priority)
    : []
  const matched = new Set<string>()
  const printed = printedRows.map((r) => {
    const norm = str(r.printed_norm)
    const index = tokens.findIndex((t) => t.variants.includes(norm))
    const token = tokens[index]
    if (token) {
      matched.add(token.norm)
      // The bank split the number in two: both pieces are the reference.
      const next = tokens[index + 1]
      if (next && norm.endsWith(next.norm) && norm !== token.norm) matched.add(next.norm)
    }
    return { item: toItem(r), fragment: token?.text ?? str(r.printed) }
  })
  const matchedFragments = [...matched]

  const ranges = amountRanges(txn, meta.currencies, meta.rates)
  const exactRows = ranges.length
    ? await loadItems(
        db,
        `(${ranges.join(' OR ')}) AND ${issuedBy(txn)} AND kind IN ${kindsForSign(txn.amount)}`,
        priority,
        `ORDER BY issue_date LIMIT ${EXACT_LIMIT}`,
      )
    : []

  const named = await findParties(db, txn, priority)
  const partyIds = new Set<string>([
    ...proposalItems.map((r) => str(r.party_id)),
    ...printedRows.map((r) => str(r.party_id)),
    ...named.map((r) => str(r.party_id)),
  ])
  partyIds.delete('')
  const partyRows = partyIds.size
    ? await loadItems(
        db,
        `party_id IN (${[...partyIds].map(lit).join(',')}) AND ${issuedBy(txn)}`,
        priority,
        `ORDER BY issue_date LIMIT ${PARTY_ITEM_LIMIT}`,
      )
    : []

  const raw: RawCandidates = {
    proposal: proposalItems.map(toItem),
    printed,
    exact: exactRows.map(toItem),
    party: partyRows.map(toItem),
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
    golden: meta.hasGolden ? await goldenRowsFor(db, txn.id, lit) : [],
  }
}

export async function searchItems(db: Db, term: string): Promise<Item[]> {
  const trimmed = term.trim()
  if (trimmed.length < 2) return []
  const asNumber = parseAmount(trimmed)
  const pattern = lit(`%${trimmed.toUpperCase()}%`)
  const norm = trimmed.toUpperCase().replace(/[^A-Z0-9]/g, '')
  const conditions = [
    `upper(item_id) LIKE ${pattern}`,
    `upper(party_name) LIKE ${pattern}`,
    `upper(party_id) LIKE ${pattern}`,
    `upper(po) LIKE ${pattern}`,
    ...(norm ? [`printed_norm LIKE ${lit(`%${norm}%`)}`] : []),
    ...(asNumber !== null ? [`open_cents = ${Math.round(Math.abs(asNumber) * 100)}`] : []),
  ]
  const rows = await loadItems(db, `(${conditions.join(' OR ')})`, 'high', 'ORDER BY issue_date LIMIT 30')
  return rows.map((r) => rowToItem(r))
}

export type PdfHit = { file: string; page: number; snippet: string }

const SNIPPET_RADIUS = 70
const PDF_HITS = 6

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
  // The document that mentions the most of what this movement refers to goes first: O opens it.
  const hits = norms.map((n) => `contains(text_norm, ${lit(n)})::INT`).join(' + ')
  const rows = await db.query(
    `SELECT file, page, text FROM (SELECT file, page, text, ${hits} AS hits FROM pdf_text) WHERE hits > 0 ORDER BY hits DESC LIMIT ${PDF_HITS}`,
    priority,
  )
  return rows.map((r) => {
    const text = str(r.text)
    const needle = clean.find((t) => text.toUpperCase().includes(t.toUpperCase())) ?? clean[0]
    return { file: str(r.file), page: numOr(r.page, 1), snippet: makeSnippet(text, needle) }
  })
}
