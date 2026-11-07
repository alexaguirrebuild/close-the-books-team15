import Papa from 'papaparse'
import {
  classifyFile,
  isCompleteMapping,
  isCsvName,
  resolveColumns,
  ROLE_SCHEMA,
  type ColumnMapping,
  type FileRole,
} from './schema'

/** Opening is deferred: a phase can have tens of thousands of PDFs nobody will read. */
export type SourceFile = { path: string; open: () => Promise<File> }

export const fileSource = (path: string, file: File): SourceFile => ({ path, open: async () => file })

export type PlannedFile = {
  role: FileRole
  path: string
  file: File
  compressed: boolean
  headers: string[]
  mapping: ColumnMapping
  complete: boolean
}

export type Plan = {
  name: string
  phase: string | null
  files: Partial<Record<FileRole, PlannedFile>>
  /** Matching stages: they add source and confidence to what the merged file proposes. */
  enrichments: PlannedFile[]
  proposalOptions: string[]
  pdfs: SourceFile[]
  ignored: string[]
}

export type PlanOptions = { phase?: string | null; proposalPath?: string | null }

const HEADER_BYTES = 64 * 1024
const GZIP_MAGIC = [0x1f, 0x8b]

export async function isGzip(file: File): Promise<boolean> {
  const head = new Uint8Array(await file.slice(0, GZIP_MAGIC.length).arrayBuffer())
  return GZIP_MAGIC.every((byte, index) => head[index] === byte)
}

/** The slice is cut mid-stream on purpose: only the first block is needed. */
async function inflateHead(file: File): Promise<string> {
  const stream = file.slice(0, HEADER_BYTES).stream().pipeThrough(new DecompressionStream('gzip'))
  const reader = stream.getReader()
  const { value } = await reader.read()
  void reader.cancel().catch(() => undefined)
  return new TextDecoder().decode(value)
}

export async function readHeaders(file: File): Promise<string[]> {
  const head = (await isGzip(file)) ? await inflateHead(file) : await file.slice(0, HEADER_BYTES).text()
  const parsed = Papa.parse<string[]>(head.replace(/^﻿/, ''), { preview: 1 })
  return (parsed.data[0] ?? []).map((h) => h.trim())
}

async function planFile(role: FileRole, source: SourceFile): Promise<PlannedFile> {
  const file = await source.open()
  const headers = await readHeaders(file)
  const mapping = resolveColumns(ROLE_SCHEMA[role], headers)
  return {
    role,
    path: source.path,
    file,
    compressed: await isGzip(file),
    headers,
    mapping,
    complete: isCompleteMapping(ROLE_SCHEMA[role], mapping),
  }
}

const isStage = (path: string) => /(^|\/)matches\//.test(path)
const ENGINE_RULE_COLUMN = /^(rule|regla)$/i

/** The most finished file wins: named proposals and the engine's explained rows, then the merged submission, then the baseline. */
function proposalRank(path: string): number {
  const name = (path.split('/').at(-1) ?? '').toLowerCase()
  if (isStage(path)) return 3
  if (/^(propuestas|proposals|explain_)/.test(name)) return 0
  if (/^(submission|sub_|merged)/.test(name)) return 1
  if (/^baseline/.test(name)) return 2
  return 4
}

async function pickProposal(candidates: SourceFile[], wanted: string | null | undefined): Promise<SourceFile | null> {
  const chosen = candidates.find((source) => source.path === wanted)
  if (chosen) return chosen
  const ranked = [...candidates].sort((a, b) => proposalRank(a.path) - proposalRank(b.path))
  const best = ranked[0]
  if (!best) return null
  const peers = ranked.filter((source) => proposalRank(source.path) === proposalRank(best.path))
  if (peers.length === 1) return best
  const dated = await Promise.all(peers.map(async (source) => ({ source, at: (await source.open()).lastModified })))
  return dated.reduce((latest, next) => (next.at > latest.at ? next : latest)).source
}

export async function buildPlan(name: string, sources: SourceFile[], options: PlanOptions = {}): Promise<Plan> {
  const files: Plan['files'] = {}
  const pdfs: SourceFile[] = []
  const ignored: string[] = []
  const proposals: SourceFile[] = []

  for (const source of sources) {
    if (source.path.toLowerCase().endsWith('.pdf')) {
      pdfs.push(source)
      continue
    }
    const role = classifyFile(source.path)
    if (role === 'proposals') {
      proposals.push(source)
      continue
    }
    if (!role || files[role]) {
      if (isCsvName(source.path)) ignored.push(source.path)
      continue
    }
    files[role] = await planFile(role, source)
  }

  const primary = await pickProposal(proposals, options.proposalPath)
  if (primary) files.proposals = await planFile('proposals', primary)
  // The engine says where each row came from: the stages and their confidence belong to another pipeline.
  const fromEngine = ENGINE_RULE_COLUMN.test(files.proposals?.mapping.source ?? '')
  const stages = fromEngine ? [] : proposals.filter((source) => isStage(source.path) && source.path !== primary?.path)
  const enrichments = await Promise.all(stages.map((source) => planFile('proposals', source)))

  return {
    name,
    phase: options.phase ?? null,
    files,
    enrichments,
    proposalOptions: proposals.map((source) => source.path),
    pdfs,
    ignored,
  }
}

export function withMapping(plan: Plan, role: FileRole, field: string, column: string): Plan {
  const current = plan.files[role]
  if (!current) return plan
  const mapping = { ...current.mapping, [field]: column || undefined }
  return {
    ...plan,
    files: {
      ...plan.files,
      [role]: { ...current, mapping, complete: isCompleteMapping(ROLE_SCHEMA[role], mapping) },
    },
  }
}

export type PlanProblem = { role?: FileRole; message: string }

export function planProblems(plan: Plan): PlanProblem[] {
  const problems: PlanProblem[] = []
  if (!plan.files.bank) {
    problems.push({ message: 'Falta bank_transactions.csv en la carpeta. Si los datos son enlaces simbólicos, elige la carpeta original: el navegador no los sigue.' })
  }
  if (!plan.files.ar && !plan.files.ap && !plan.files.cn && !plan.files.open) {
    problems.push({ message: 'Falta al menos un fichero de partidas: ar_invoices, ap_invoices, credit_notes u open_items_at_start.' })
  }
  for (const file of Object.values(plan.files)) {
    if (file && !file.complete) {
      problems.push({ role: file.role, message: `Asigna las columnas obligatorias de ${file.path}.` })
    }
  }
  return problems
}

/** Identifies a dataset across reloads so the review log survives without a backend. */
export function datasetKey(plan: Plan): string {
  const bank = plan.files.bank?.file
  return `${plan.name}|${bank?.size ?? 0}|${bank?.lastModified ?? 0}`
}
