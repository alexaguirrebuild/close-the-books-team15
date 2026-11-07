import { EXCEPTIONS } from '../domain/types'
import type { FxRate } from '../domain/types'
import { ident, lit, numOr, str, type Db } from './db'
import type { PlannedFile, Plan } from './preflight'
import type { ColumnMapping, FileRole } from './schema'

export type Progress = { step: string; done: number; total: number }

export type DatasetMeta = {
  name: string
  txnCount: number
  itemCount: number
  openItemCount: number
  proposalTxnCount: number
  hasGolden: boolean
  hasProposals: boolean
  hasOpenItems: boolean
  categories: string[]
  currencies: string[]
  accounts: string[]
  rates: FxRate[]
  discounts: Map<string, number>
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
CREATE OR REPLACE MACRO pnorm(x) AS regexp_replace(upper(x), '[^A-Z0-9]', '', 'g');
CREATE OR REPLACE MACRO isodate(x) AS coalesce(strftime(TRY_CAST(nz(x) AS DATE), '%Y-%m-%d'), nz(x));
`

const fileName = (role: FileRole) => `src_${role}.csv`
const col = (mapping: ColumnMapping, field: string) =>
  mapping[field] ? ident(mapping[field] as string) : 'NULL'
const reader = (role: FileRole) =>
  `read_csv(${lit(fileName(role))}, all_varchar=true, header=true, auto_detect=true, null_padding=true)`

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
            ${col(m, 'description')} AS description
     FROM ${reader('bank')}`,
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
                 nz(${col(m, 'terms')}) AS terms
          FROM ${reader(file.role)}`
}

async function ingestParties(db: Db, plan: Plan): Promise<void> {
  const selects = (['customers', 'suppliers'] as const).flatMap((role) => {
    const file = plan.files[role]
    if (!file) return []
    return [
      `SELECT trim(${col(file.mapping, 'id')}) AS party_id, nz(${col(file.mapping, 'name')}) AS name FROM ${reader(role)}`,
    ]
  })
  const source = selects.length
    ? selects.join(' UNION ALL ')
    : 'SELECT NULL::VARCHAR AS party_id, NULL::VARCHAR AS name WHERE false'
  await exec(
    db,
    `CREATE OR REPLACE TABLE parties AS
     SELECT party_id, any_value(name) AS name, upper(any_value(name)) AS name_norm
     FROM (${source}) WHERE party_id IS NOT NULL GROUP BY party_id`,
  )
}

async function ingestItems(db: Db, plan: Plan, warnings: string[]): Promise<void> {
  const files = (['ar', 'ap', 'cn'] as const).flatMap((role) => plan.files[role] ?? [])
  const open = plan.files.open
  const openCte = open
    ? `SELECT trim(${col(open.mapping, 'id')}) AS item_id, any_value(num(${col(open.mapping, 'openAmount')})) AS open_amount
       FROM ${reader('open')} GROUP BY 1`
    : 'SELECT NULL::VARCHAR AS item_id, NULL::DOUBLE AS open_amount WHERE false'
  await exec(db, `CREATE OR REPLACE TABLE open_set AS ${openCte}`)
  await exec(
    db,
    `CREATE OR REPLACE TABLE items AS
     SELECT i.item_id,
            CASE WHEN i.item_id LIKE 'ARI%' OR i.role = 'ar' THEN 'AR'
                 WHEN i.item_id LIKE 'API%' OR i.role = 'ap' THEN 'AP'
                 WHEN i.item_id LIKE 'ARC%' OR i.role = 'cn' THEN 'CN' ELSE 'OTHER' END AS kind,
            i.printed,
            pnorm(i.printed) AS printed_norm,
            i.party_id,
            coalesce(i.party_name, p.name, i.party_id) AS party_name,
            i.amount,
            coalesce(o.open_amount, i.file_open, i.amount) AS open_amount,
            CAST(round(abs(coalesce(o.open_amount, i.file_open, i.amount)) * 100) AS BIGINT) AS open_cents,
            i.currency,
            i.due_date,
            i.terms,
            ${open ? 'o.item_id IS NOT NULL' : 'true'} AS is_open
     FROM (${files.map(itemSelect).join(' UNION ALL ')}) i
     LEFT JOIN open_set o ON i.item_id = o.item_id
     LEFT JOIN parties p ON i.party_id = p.party_id`,
  )
  const duplicates = await scalar(db, 'SELECT count(*) - count(DISTINCT item_id) FROM items')
  if (duplicates) warnings.push(`${duplicates} item_id están repetidos entre los ficheros de partidas.`)
  const unknownOpen = await scalar(
    db,
    'SELECT count(*) FROM open_set o WHERE NOT EXISTS (SELECT 1 FROM items i WHERE i.item_id = o.item_id)',
  )
  if (unknownOpen) warnings.push(`${unknownOpen} partidas abiertas no aparecen en los ficheros de partidas.`)
  await exec(db, 'DROP TABLE open_set; CHECKPOINT')
}

async function ingestProposals(db: Db, plan: Plan, warnings: string[]): Promise<void> {
  const file = plan.files.proposals
  if (!file) {
    await exec(
      db,
      `CREATE OR REPLACE TABLE prop (rid INTEGER, txn_id VARCHAR, item_id VARCHAR, amount DOUBLE,
         exception VARCHAR, category VARCHAR, conf DOUBLE, reason VARCHAR)`,
    )
  } else {
    const m = file.mapping
    await exec(
      db,
      `CREATE OR REPLACE TABLE prop_raw AS
       SELECT trim(${col(m, 'txn')}) AS txn_id, nz(${col(m, 'item')}) AS item_id, num(${col(m, 'amount')}) AS amount,
              upper(nz(${col(m, 'exception')})) AS exception, nz(${col(m, 'category')}) AS category,
              num(${col(m, 'confidence')}) AS conf, ${col(m, 'reason')} AS reason
       FROM ${reader('proposals')}`,
    )
    const maxConf = await scalar(db, 'SELECT max(conf) FROM prop_raw')
    const scale = maxConf > 1 ? 100 : 1
    await exec(
      db,
      `CREATE OR REPLACE TABLE prop AS
       SELECT t.rid, p.txn_id, p.item_id, p.amount, p.exception, p.category, p.conf / ${scale} AS conf, p.reason
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
            any_value(p.exception) AS exception,
            any_value(p.category) AS category,
            any_value(p.reason) AS reason,
            coalesce(bool_or(i.currency <> t.currency), false) AS fx
     FROM prop p
     JOIN txn t ON t.rid = p.rid
     LEFT JOIN items i ON i.item_id = p.item_id
     GROUP BY p.rid`,
  )
  await exec(
    db,
    `CREATE OR REPLACE TABLE txn_q AS
     SELECT t.rid, t.id, t.date, t.amount, abs(t.amount) AS abs_amount, t.currency, t.account, t.description,
            s.conf, s.n_items, s.party_id, s.party_name, s.exception, s.category, s.reason,
            CASE WHEN s.rid IS NULL THEN 'sin_propuesta'
                 WHEN s.exception IS NOT NULL THEN 'excepcion'
                 WHEN s.fx THEN 'multidivisa'
                 WHEN s.n_items > 1 THEN 'uno_a_n'
                 WHEN s.n_items = 1 THEN 'uno_a_uno'
                 ELSE 'categoria' END AS case_type
     FROM txn t LEFT JOIN prop_sum s ON s.rid = t.rid ORDER BY t.rid`,
  )
  // txn_q already holds every column: keeping both tables would store the concepts twice.
  await exec(
    db,
    `DROP TABLE txn;
     CREATE VIEW txn AS SELECT rid, id, date, amount, currency, account, description FROM txn_q;
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
            nz(${col(m, 'category')}) AS category, nz(${col(m, 'exception')}) AS exception
     FROM ${reader('golden')}`,
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
     FROM ${reader('pdfText')}`,
  )
  return scalar(db, 'SELECT count(*) FROM pdf_text')
}

async function readCategories(db: Db, plan: Plan): Promise<string[]> {
  const file = plan.files.categories
  if (!file) return []
  const rows = await db.query(
    `SELECT DISTINCT cat FROM (SELECT nz(${col(file.mapping, 'category')}) AS cat FROM ${reader('categories')}) WHERE cat IS NOT NULL ORDER BY cat`,
  )
  return rows.map((r) => str(r.cat))
}

async function readRates(db: Db, plan: Plan): Promise<FxRate[]> {
  const file = plan.files.fx
  if (!file) return []
  const m = file.mapping
  const rows = await db.query(
    `SELECT * FROM (
       SELECT isodate(${col(m, 'date')}) AS d, upper(trim(${col(m, 'base')})) AS b,
              upper(trim(${col(m, 'quote')})) AS q, num(${col(m, 'rate')}) AS r
       FROM ${reader('fx')}
     ) WHERE r > 0`,
  )
  return rows.map((r) => ({ date: str(r.d), base: str(r.b), quote: str(r.q), rate: numOr(r.r, 1) }))
}

async function readDiscounts(db: Db, plan: Plan): Promise<Map<string, number>> {
  const file = plan.files.terms
  if (!file?.mapping.discount) return new Map()
  const rows = await db.query(
    `SELECT trim(${col(file.mapping, 'id')}) AS id, num(${col(file.mapping, 'discount')}) AS pct FROM ${reader('terms')}`,
  )
  return new Map(
    rows.flatMap((r) => {
      const pct = numOr(r.pct, 0)
      if (!pct) return []
      return [[str(r.id), pct < 1 ? pct * 100 : pct] as [string, number]]
    }),
  )
}

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
  await Promise.all(planned.map((file) => db.registerFile(fileName(file.role), file.file)))
  const total = 7
  const step = (label: string, done: number) => onProgress({ step: label, done, total })

  await exec(db, SETUP)
  step('Leyendo movimientos', 0)
  const txnCount = await ingestTxns(db, bank, warnings)
  await exec(db, 'CHECKPOINT')
  step('Leyendo maestros', 1)
  await ingestParties(db, plan)
  const categories = await readCategories(db, plan)
  const rates = await readRates(db, plan)
  const discounts = await readDiscounts(db, plan)
  step('Leyendo e indexando partidas', 2)
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
    openItemCount: await scalar(db, 'SELECT count(*) FROM items WHERE is_open'),
    proposalTxnCount: await scalar(db, 'SELECT count(*) FROM prop_sum'),
    hasGolden: Boolean(plan.files.golden),
    hasProposals: Boolean(plan.files.proposals),
    hasOpenItems: Boolean(plan.files.open),
    categories,
    currencies: await list(db, 'SELECT DISTINCT currency AS v FROM txn ORDER BY 1'),
    accounts: await list(db, 'SELECT DISTINCT account AS v FROM txn ORDER BY 1'),
    rates,
    discounts,
    pdfCount: plan.pdfs.length,
    pdfTextPages,
    warnings,
  }
  step('Listo', total)
  return meta
}
