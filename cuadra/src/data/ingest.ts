import { EXCEPTIONS } from '../domain/types'
import type { FxRate } from '../domain/types'
import { ident, lit, numOr, str, type Db } from './db'
import type { PlannedFile, Plan } from './preflight'
import type { ColumnMapping } from './schema'

export type Progress = { step: string; done: number; total: number }

export type PaymentTerm = { pct: number; days: number }

export type DatasetMeta = {
  name: string
  txnCount: number
  itemCount: number
  openItemCount: number
  proposalTxnCount: number
  hasGolden: boolean
  hasProposals: boolean
  hasConfidence: boolean
  hasOpenItems: boolean
  phase: string | null
  categories: string[]
  categoryLabels: Map<string, string>
  currencies: string[]
  accounts: string[]
  entities: string[]
  rates: FxRate[]
  terms: Map<string, PaymentTerm>
  pdfCount: number
  pdfTextPages: number
  warnings: string[]
}

/** Without COMPRESS an in-memory table stays raw and TRAIN does not fit in the 4 GB of WASM. */
const SETUP = `
ATTACH IF NOT EXISTS ':memory:' AS cuadra (COMPRESS);
USE cuadra;
DROP VIEW IF EXISTS txn;
CREATE OR REPLACE MACRO nz(x) AS NULLIF(trim(x), '');
CREATE OR REPLACE MACRO num(x) AS TRY_CAST(CASE
  WHEN nz(x) IS NULL THEN NULL
  WHEN contains(x, ',') AND contains(x, '.') AND strpos(reverse(x), '.') < strpos(reverse(x), ',') THEN replace(trim(x), ',', '')
  WHEN contains(x, ',') AND contains(x, '.') THEN replace(replace(trim(x), '.', ''), ',', '.')
  WHEN contains(x, ',') THEN replace(trim(x), ',', '.')
  ELSE trim(x) END AS DOUBLE);
CREATE OR REPLACE MACRO pnorm(x) AS regexp_replace(upper(strip_accents(x)), '[^A-Z0-9]', '', 'g');
CREATE OR REPLACE MACRO isodate(x) AS coalesce(strftime(TRY_CAST(nz(x) AS DATE), '%Y-%m-%d'), nz(x));
`

/** DuckDB decides how to read a file from its extension. */
const fileName = (file: PlannedFile, suffix = '') => `src_${file.role}${suffix}.csv${file.compressed ? '.gz' : ''}`
const col = (mapping: ColumnMapping, field: string) =>
  mapping[field] ? ident(mapping[field] as string) : 'NULL'
/** Quotes are set by hand: the first quoted value of a master can be beyond what DuckDB samples. */
const reader = (file: PlannedFile, suffix = '') =>
  `read_csv(${lit(fileName(file, suffix))}, all_varchar=true, header=true, auto_detect=true, null_padding=true, quote='"', escape='"')`

async function exec(db: Db, script: string): Promise<void> {
  await db.query(script)
}

async function scalar(db: Db, sql: string): Promise<number> {
  const [row] = await db.query(sql)
  return numOr(Object.values(row ?? {})[0], 0)
}

async function ingestTxns(db: Db, file: PlannedFile, warnings: string[]): Promise<number> {
  const m = file.mapping
  await exec(
    db,
    `CREATE OR REPLACE TABLE txn AS
     SELECT CAST(row_number() OVER () AS INTEGER) AS rid,
            trim(${col(m, 'id')}) AS id,
            isodate(${col(m, 'date')}) AS date,
            num(${col(m, 'amount')}) AS amount,
            upper(trim(${col(m, 'currency')})) AS currency,
            trim(${col(m, 'account')}) AS account,
            coalesce(${col(m, 'description')}, '') AS description,
            nz(${col(m, 'entity')}) AS entity,
            nz(${col(m, 'bank')}) AS bank,
            nz(${col(m, 'counterparty')}) AS counterparty,
            nz(${col(m, 'counterpartyAccount')}) AS counterparty_account,
            nz(${col(m, 'endToEnd')}) AS end_to_end,
            nz(${col(m, 'remittance')}) AS remittance,
            nz(${col(m, 'ref1')}) AS ref1,
            nz(${col(m, 'ref2')}) AS ref2,
            num(${col(m, 'origAmount')}) AS orig_amount,
            nz(${col(m, 'origCurrency')}) AS orig_currency
     FROM ${reader(file)}`,
  )
  const bad = await scalar(db, 'SELECT count(*) FROM txn WHERE amount IS NULL')
  if (bad) warnings.push(`${bad} movimientos tienen un importe que no se puede leer.`)
  const duplicates = await scalar(db, 'SELECT count(*) - count(DISTINCT id) FROM txn')
  if (duplicates) warnings.push(`${duplicates} bank_txn_id están repetidos en bank_transactions.csv.`)
  return scalar(db, 'SELECT count(*) FROM txn')
}

function itemSelect(file: PlannedFile): string {
  const m = file.mapping
  return `SELECT trim(${col(m, 'id')}) AS item_id,
                 ${lit(file.role)} AS role,
                 trim(${col(m, 'printed')}) AS printed,
                 trim(${col(m, 'party')}) AS party_id,
                 nz(${col(m, 'partyName')}) AS party_name,
                 num(${col(m, 'amount')}) AS amount,
                 num(${col(m, 'openAmount')}) AS file_open,
                 upper(trim(${col(m, 'currency')})) AS currency,
                 isodate(${col(m, 'dueDate')}) AS due_date,
                 isodate(${col(m, 'issueDate')}) AS issue_date,
                 nz(${col(m, 'terms')}) AS terms,
                 nz(${col(m, 'po')}) AS po
          FROM ${reader(file)}`
}

async function ingestParties(db: Db, plan: Plan): Promise<void> {
  const selects = (['customers', 'suppliers'] as const).flatMap((role) => {
    const file = plan.files[role]
    if (!file) return []
    const m = file.mapping
    return [
      `SELECT trim(${col(m, 'id')}) AS party_id, nz(${col(m, 'name')}) AS name, nz(${col(m, 'tradeName')}) AS trade_name,
              nz(${col(m, 'iban')}) AS iban, nz(${col(m, 'entity')}) AS entity, nz(${col(m, 'terms')}) AS terms
       FROM ${reader(file)}`,
    ]
  })
  const source = selects.length
    ? selects.join(' UNION ALL ')
    : `SELECT NULL::VARCHAR AS party_id, NULL::VARCHAR AS name, NULL::VARCHAR AS trade_name, NULL::VARCHAR AS iban,
              NULL::VARCHAR AS entity, NULL::VARCHAR AS terms WHERE false`
  // The bank cuts names short, so a party is found by the start of its name, without punctuation.
  await exec(
    db,
    `CREATE OR REPLACE TABLE parties AS
     SELECT party_id, any_value(name) AS name, left(pnorm(any_value(name)), ${PARTY_KEY_LENGTH}) AS name_key,
            left(pnorm(any_value(trade_name)), ${PARTY_KEY_LENGTH}) AS trade_key,
            any_value(iban) AS iban, any_value(entity) AS entity, any_value(terms) AS terms
     FROM (${source}) WHERE party_id IS NOT NULL GROUP BY party_id`,
  )
}

const ITEM_KIND = `CASE WHEN i.item_id LIKE 'ARC%' OR i.item_id LIKE 'APC%' THEN 'CN'
                        WHEN i.item_id LIKE 'ARI%' THEN 'AR'
                        WHEN i.item_id LIKE 'API%' THEN 'AP'
                        WHEN i.role = 'ar' THEN 'AR'
                        WHEN i.role = 'ap' THEN 'AP'
                        WHEN i.role = 'cn' THEN 'CN'
                        ELSE 'OTHER' END`

/**
 * Items are what the phase issues plus what was still open when it began.
 * The open file also gives the balance of an item the phase lists again.
 */
async function ingestItems(db: Db, plan: Plan, warnings: string[]): Promise<void> {
  const files = (['ar', 'ap', 'cn'] as const).flatMap((role) => plan.files[role] ?? [])
  const open = plan.files.open
  const m = open?.mapping ?? {}
  const openCte = open
    ? `SELECT trim(${col(m, 'id')}) AS item_id, any_value(num(${col(m, 'openAmount')})) AS open_amount,
              any_value(trim(${col(m, 'printed')})) AS printed, any_value(trim(${col(m, 'party')})) AS party_id,
              any_value(num(${col(m, 'amount')})) AS amount, any_value(upper(trim(${col(m, 'currency')}))) AS currency,
              any_value(isodate(${col(m, 'dueDate')})) AS due_date, any_value(isodate(${col(m, 'issueDate')})) AS issue_date
       FROM ${reader(open)} GROUP BY 1`
    : `SELECT NULL::VARCHAR AS item_id, NULL::DOUBLE AS open_amount, NULL::VARCHAR AS printed, NULL::VARCHAR AS party_id,
              NULL::DOUBLE AS amount, NULL::VARCHAR AS currency, NULL::VARCHAR AS due_date, NULL::VARCHAR AS issue_date WHERE false`
  await exec(db, `CREATE OR REPLACE TABLE open_set AS ${openCte}`)
  const phaseSelect = files.length
    ? files.map(itemSelect).join(' UNION ALL ')
    : `SELECT NULL::VARCHAR AS item_id, NULL::VARCHAR AS role, NULL::VARCHAR AS printed, NULL::VARCHAR AS party_id,
              NULL::VARCHAR AS party_name, NULL::DOUBLE AS amount, NULL::DOUBLE AS file_open, NULL::VARCHAR AS currency,
              NULL::VARCHAR AS due_date, NULL::VARCHAR AS issue_date, NULL::VARCHAR AS terms, NULL::VARCHAR AS po WHERE false`
  await exec(db, `CREATE OR REPLACE TABLE phase_items AS ${phaseSelect}`)
  // Ordered by party, looking up the items of a party skips most of the table. Sorting TRAIN does not fit in the 4 GB of WASM.
  const isTooLargeToSort = (await scalar(db, 'SELECT count(*) FROM phase_items')) > MAX_ITEMS_TO_SORT
  await exec(
    db,
    `CREATE OR REPLACE TABLE items AS
     SELECT i.item_id,
            ${ITEM_KIND} AS kind,
            i.printed,
            pnorm(i.printed) AS printed_norm,
            i.party_id,
            coalesce(i.party_name, p.name, i.party_id) AS party_name,
            i.amount,
            coalesce(o.open_amount, i.file_open, i.amount) AS open_amount,
            CAST(round(abs(coalesce(o.open_amount, i.file_open, i.amount)) * 100) AS BIGINT) AS open_cents,
            i.currency,
            coalesce(i.due_date, i.issue_date) AS due_date,
            i.issue_date,
            coalesce(i.terms, p.terms) AS terms,
            i.po
     FROM (
       SELECT * FROM phase_items
       UNION ALL
       SELECT s.item_id, 'open' AS role, s.printed, s.party_id, NULL AS party_name, s.amount, s.open_amount AS file_open,
              s.currency, s.due_date, s.issue_date, NULL AS terms, NULL AS po
       FROM open_set s
       WHERE s.currency IS NOT NULL AND NOT EXISTS (SELECT 1 FROM phase_items f WHERE f.item_id = s.item_id)
     ) i
     LEFT JOIN open_set o ON i.item_id = o.item_id
     LEFT JOIN parties p ON i.party_id = p.party_id
     ${isTooLargeToSort ? '' : 'ORDER BY i.party_id, i.issue_date'}`,
  )
  const duplicates = await scalar(db, 'SELECT count(*) - count(DISTINCT item_id) FROM items')
  if (duplicates) warnings.push(`${duplicates} item_id están repetidos entre los ficheros de partidas.`)
  const unknownOpen = await scalar(
    db,
    'SELECT count(*) FROM open_set o WHERE NOT EXISTS (SELECT 1 FROM items i WHERE i.item_id = o.item_id)',
  )
  if (unknownOpen) warnings.push(`${unknownOpen} partidas abiertas no aparecen en los ficheros de partidas.`)
  await exec(db, 'DROP TABLE phase_items; DROP TABLE open_set; CHECKPOINT')
}

const PROPOSAL_COLUMNS = `(rid INTEGER, txn_id VARCHAR, item_id VARCHAR, amount DOUBLE, exception VARCHAR, category VARCHAR,
  conf DOUBLE, reason VARCHAR, source VARCHAR, advice_id VARCHAR, line_no VARCHAR)`

/** The merged file has only the official columns: the stages say where each row came from. */
async function ingestEnrichments(db: Db, plan: Plan): Promise<string> {
  if (!plan.enrichments.length) return ''
  const selects = plan.enrichments.map((file, index) => {
    const m = file.mapping
    return `SELECT trim(${col(m, 'txn')}) AS txn_id, nz(${col(m, 'item')}) AS item_id, num(${col(m, 'confidence')}) AS conf,
                   nz(${col(m, 'source')}) AS source, nz(${col(m, 'adviceId')}) AS advice_id, nz(${col(m, 'lineNo')}) AS line_no
            FROM ${reader(file, `_stage${index}`)}`
  })
  await exec(
    db,
    `CREATE OR REPLACE TABLE prop_stage AS
     SELECT txn_id, item_id, any_value(conf) AS conf, any_value(source) AS source,
            any_value(advice_id) AS advice_id, any_value(line_no) AS line_no
     FROM (${selects.join(' UNION ALL ')}) GROUP BY txn_id, item_id`,
  )
  return 'prop_stage'
}

async function ingestProposals(db: Db, plan: Plan, warnings: string[]): Promise<void> {
  const file = plan.files.proposals
  if (!file) {
    await exec(db, `CREATE OR REPLACE TABLE prop ${PROPOSAL_COLUMNS}`)
  } else {
    const m = file.mapping
    await exec(
      db,
      `CREATE OR REPLACE TABLE prop_raw AS
       SELECT trim(${col(m, 'txn')}) AS txn_id, nz(${col(m, 'item')}) AS item_id, abs(num(${col(m, 'amount')})) AS amount,
              upper(nz(${col(m, 'exception')})) AS exception, nz(${col(m, 'category')}) AS category,
              num(${col(m, 'confidence')}) AS conf, ${col(m, 'reason')} AS reason, nz(${col(m, 'source')}) AS source,
              nz(${col(m, 'adviceId')}) AS advice_id, nz(${col(m, 'lineNo')}) AS line_no
       FROM ${reader(file)}`,
    )
    const stage = await ingestEnrichments(db, plan)
    if (stage) {
      await exec(
        db,
        `CREATE OR REPLACE TABLE prop_raw AS
         SELECT p.txn_id, p.item_id, p.amount, p.exception, p.category, coalesce(p.conf, e.conf) AS conf, p.reason,
                coalesce(p.source, e.source) AS source, coalesce(p.advice_id, e.advice_id) AS advice_id,
                coalesce(p.line_no, e.line_no) AS line_no
         FROM prop_raw p LEFT JOIN ${stage} e ON p.txn_id = e.txn_id AND p.item_id IS NOT DISTINCT FROM e.item_id;
         DROP TABLE ${stage}`,
      )
    }
    const maxConf = await scalar(db, 'SELECT max(conf) FROM prop_raw')
    const scale = maxConf > 1 ? 100 : 1
    await exec(
      db,
      `CREATE OR REPLACE TABLE prop AS
       SELECT t.rid, p.txn_id, p.item_id, p.amount, p.exception, p.category, p.conf / ${scale} AS conf, p.reason,
              p.source, p.advice_id, p.line_no
       FROM prop_raw p JOIN txn t ON p.txn_id = t.id ORDER BY t.rid`,
    )
    const orphan = await scalar(
      db,
      'SELECT count(*) FROM prop_raw p WHERE NOT EXISTS (SELECT 1 FROM txn t WHERE t.id = p.txn_id)',
    )
    if (orphan) warnings.push(`${orphan} filas de propuestas apuntan a movimientos que no existen y se han ignorado.`)
    const closedList = EXCEPTIONS.map((e) => lit(e)).join(',')
    const badException = await scalar(
      db,
      `SELECT count(*) FROM prop WHERE exception IS NOT NULL AND exception NOT IN (${closedList})`,
    )
    if (badException) warnings.push(`${badException} propuestas traen una excepción fuera de la lista cerrada.`)
    await exec(db, 'DROP TABLE prop_raw; CHECKPOINT')
  }
  await exec(
    db,
    `CREATE OR REPLACE TABLE prop_sum AS
     SELECT p.rid,
            min(p.conf) AS conf,
            count(*) AS n_rows,
            count(p.item_id) AS n_items,
            any_value(i.party_id) AS party_id,
            any_value(i.party_name) AS party_name,
            max(p.exception) AS exception,
            any_value(p.category) AS category,
            any_value(p.reason) AS reason,
            any_value(p.source) AS source,
            coalesce(bool_or(i.currency <> t.currency), false) AS fx
     FROM prop p
     JOIN txn t ON t.rid = p.rid
     LEFT JOIN items i ON i.item_id = p.item_id
     GROUP BY p.rid`,
  )
  // Rare exception classes weigh as much in the macro-F1 as common ones, so they rank higher.
  await exec(
    db,
    `CREATE OR REPLACE TABLE exception_rarity AS
     SELECT exception, 1 - n / max(n) OVER () AS rarity
     FROM (SELECT exception, count(*)::DOUBLE AS n FROM prop WHERE exception IS NOT NULL GROUP BY exception)`,
  )
  await exec(
    db,
    `CREATE OR REPLACE TABLE txn_q AS
     SELECT t.*, abs(t.amount) AS abs_amount,
            abs(t.amount) / CASE WHEN t.currency = 'EUR' THEN 1 ELSE coalesce(f.units, 1) END AS abs_eur,
            s.conf, s.n_items, s.party_id, s.party_name, s.exception, s.category, s.reason, s.source, r.rarity,
            CASE WHEN s.rid IS NULL THEN 'sin_propuesta'
                 WHEN s.exception IS NOT NULL THEN 'excepcion'
                 WHEN s.fx THEN 'multidivisa'
                 WHEN s.n_items > 1 THEN 'uno_a_n'
                 WHEN s.n_items = 1 THEN 'uno_a_uno'
                 ELSE 'categoria' END AS case_type
     FROM txn t
     ASOF LEFT JOIN fx_eur f ON t.currency = f.currency AND t.date >= f.date
     LEFT JOIN prop_sum s ON s.rid = t.rid
     LEFT JOIN exception_rarity r ON r.exception = s.exception
     ORDER BY t.rid`,
  )
  // txn_q already holds every column: keeping both tables would store the concepts twice.
  await exec(
    db,
    `DROP TABLE txn;
     CREATE VIEW txn AS SELECT rid, id, date, amount, currency, account, description, entity, bank, counterparty,
       counterparty_account, end_to_end, remittance, ref1, ref2, orig_amount, orig_currency FROM txn_q;
     CHECKPOINT`,
  )
}

async function ingestGolden(db: Db, plan: Plan): Promise<void> {
  const file = plan.files.golden
  if (!file) return
  const m = file.mapping
  await exec(
    db,
    `CREATE OR REPLACE TABLE golden AS
     SELECT trim(${col(m, 'txn')}) AS txn, nz(${col(m, 'item')}) AS item, num(${col(m, 'amount')}) AS amount,
            nz(${col(m, 'category')}) AS category, nz(${col(m, 'exception')}) AS exception,
            nz(${col(m, 'segment')}) AS segment, TRY_CAST(${col(m, 'difficulty')} AS INTEGER) AS difficulty
     FROM ${reader(file)}`,
  )
  await exec(db, 'CHECKPOINT')
}

async function ingestPdfText(db: Db, plan: Plan): Promise<number> {
  const file = plan.files.pdfText
  if (!file) {
    await exec(db, 'CREATE OR REPLACE TABLE pdf_text (file VARCHAR, page INTEGER, text VARCHAR, text_norm VARCHAR)')
    return 0
  }
  const m = file.mapping
  await exec(
    db,
    `CREATE OR REPLACE TABLE pdf_text AS
     SELECT ${col(m, 'file')} AS file, TRY_CAST(${col(m, 'page')} AS INTEGER) AS page,
            ${col(m, 'text')} AS text, pnorm(${col(m, 'text')}) AS text_norm
     FROM ${reader(file)}`,
  )
  return scalar(db, 'SELECT count(*) FROM pdf_text')
}

async function readCategories(db: Db, plan: Plan): Promise<Map<string, string>> {
  const file = plan.files.categories
  if (!file) return new Map()
  const rows = await db.query(
    `SELECT DISTINCT cat, label FROM (
       SELECT nz(${col(file.mapping, 'category')}) AS cat, nz(${col(file.mapping, 'label')}) AS label FROM ${reader(file)}
     ) WHERE cat IS NOT NULL ORDER BY cat`,
  )
  return new Map(rows.map((r) => [str(r.cat), str(r.label)]))
}

/** Rates for the screens, and per-euro units for sorting the queue across currencies. */
async function ingestRates(db: Db, plan: Plan): Promise<FxRate[]> {
  const file = plan.files.fx
  if (!file) {
    await exec(db, 'CREATE OR REPLACE TABLE fx_eur (currency VARCHAR, date VARCHAR, units DOUBLE)')
    return []
  }
  const m = file.mapping
  const quote = m.quote ? `upper(trim(${col(m, 'quote')}))` : lit('EUR')
  const rows = await db.query(
    `SELECT * FROM (
       SELECT isodate(${col(m, 'date')}) AS d, upper(trim(${col(m, 'base')})) AS b, ${quote} AS q, num(${col(m, 'rate')}) AS r
       FROM ${reader(file)}
     ) WHERE r > 0`,
  )
  // Without a quote column the file gives units of the currency per euro: 1 EUR = rate units.
  const rates = rows.map((r) =>
    m.quote
      ? { date: str(r.d), base: str(r.b), quote: str(r.q), rate: numOr(r.r, 1) }
      : { date: str(r.d), base: 'EUR', quote: str(r.b), rate: numOr(r.r, 1) },
  )
  await exec(db, 'CREATE OR REPLACE TABLE fx_eur (currency VARCHAR, date VARCHAR, units DOUBLE)')
  const perEuro = rates.flatMap((r) => {
    if (r.base === 'EUR') return [`(${lit(r.quote)}, ${lit(r.date)}, ${r.rate})`]
    if (r.quote === 'EUR') return [`(${lit(r.base)}, ${lit(r.date)}, ${1 / r.rate})`]
    return []
  })
  for (let start = 0; start < perEuro.length; start += RATE_INSERT_BATCH) {
    await exec(db, `INSERT INTO fx_eur VALUES ${perEuro.slice(start, start + RATE_INSERT_BATCH).join(',')}`)
  }
  return rates
}

async function readTerms(db: Db, plan: Plan): Promise<Map<string, PaymentTerm>> {
  const file = plan.files.terms
  if (!file?.mapping.discount) return new Map()
  const m = file.mapping
  const rows = await db.query(
    `SELECT trim(${col(m, 'id')}) AS id, num(${col(m, 'discount')}) AS pct, num(${col(m, 'discountDays')}) AS days FROM ${reader(file)}`,
  )
  return new Map(
    rows.flatMap((r) => {
      const pct = numOr(r.pct, 0)
      if (!pct) return []
      return [[str(r.id), { pct: pct < 1 ? pct * 100 : pct, days: numOr(r.days, 0) }] as [string, PaymentTerm]]
    }),
  )
}

async function countOpen(db: Db, plan: Plan): Promise<number> {
  const open = plan.files.open
  return open ? scalar(db, `SELECT count(*) FROM ${reader(open)}`) : 0
}

const PARTY_KEY_LENGTH = 16
const MAX_ITEMS_TO_SORT = 2_000_000
const RATE_INSERT_BATCH = 2000

const list = async (db: Db, sql: string) => (await db.query(sql)).map((r) => str(r.v)).filter(Boolean)

export async function ingest(
  db: Db,
  plan: Plan,
  onProgress: (progress: Progress) => void = () => undefined,
): Promise<DatasetMeta> {
  const warnings: string[] = []
  const bank = plan.files.bank
  if (!bank) throw new Error('Falta bank_transactions.csv')
  const planned = Object.values(plan.files).filter(Boolean) as PlannedFile[]
  await Promise.all(planned.map((file) => db.registerFile(fileName(file), file.file)))
  await Promise.all(plan.enrichments.map((file, index) => db.registerFile(fileName(file, `_stage${index}`), file.file)))
  const total = 7
  const step = (label: string, done: number) => onProgress({ step: label, done, total })

  await exec(db, SETUP)
  step('Leyendo movimientos', 0)
  const txnCount = await ingestTxns(db, bank, warnings)
  await exec(db, 'CHECKPOINT')
  step('Leyendo maestros', 1)
  await ingestParties(db, plan)
  const categoryLabels = await readCategories(db, plan)
  const categories = [...categoryLabels.keys()]
  const rates = await ingestRates(db, plan)
  const terms = await readTerms(db, plan)
  step('Leyendo e indexando partidas', 2)
  const openItemCount = await countOpen(db, plan)
  await ingestItems(db, plan, warnings)
  step('Leyendo propuestas', 3)
  await ingestProposals(db, plan, warnings)
  step('Leyendo golden', 4)
  await ingestGolden(db, plan)
  step('Indexando texto de PDF', 5)
  const pdfTextPages = await ingestPdfText(db, plan)
  await exec(
    db,
    `CREATE OR REPLACE TABLE decision_rows (rid INTEGER, txn_id VARCHAR, item_id VARCHAR, amount DOUBLE,
       category VARCHAR, exception VARCHAR, source VARCHAR, batch VARCHAR, ts BIGINT)`,
  )
  step('Preparando la cola', 6)

  if (!categories.length) warnings.push('Sin masters/categories.csv no se puede asignar category.')
  if (!plan.files.proposals) warnings.push('Sin propuestas.csv: la cola no tiene confianza y nada se confirma en un paso.')
  if (plan.pdfs.length && !plan.files.pdfText) {
    warnings.push(`Hay ${plan.pdfs.length} PDF sin indexar. Ejecuta npm run pdf:index sobre la carpeta para poder buscar en ellos.`)
  }

  const meta: DatasetMeta = {
    name: plan.name,
    txnCount,
    itemCount: await scalar(db, 'SELECT count(*) FROM items'),
    openItemCount,
    proposalTxnCount: await scalar(db, 'SELECT count(*) FROM prop_sum'),
    hasGolden: Boolean(plan.files.golden),
    hasProposals: Boolean(plan.files.proposals),
    hasConfidence: (await scalar(db, 'SELECT count(conf) FROM prop')) > 0,
    hasOpenItems: Boolean(plan.files.open),
    phase: plan.phase,
    categories,
    categoryLabels,
    currencies: await list(db, 'SELECT DISTINCT currency AS v FROM txn ORDER BY 1'),
    accounts: await list(db, 'SELECT DISTINCT account AS v FROM txn ORDER BY 1'),
    entities: await list(db, 'SELECT DISTINCT entity AS v FROM txn WHERE entity IS NOT NULL ORDER BY 1'),
    rates,
    terms,
    pdfCount: plan.pdfs.length,
    pdfTextPages,
    warnings,
  }
  step('Listo', total)
  return meta
}
