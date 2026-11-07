import Papa from 'papaparse'
import {
  classifyFile,
  isCompleteMapping,
  resolveColumns,
  ROLE_SCHEMA,
  type ColumnMapping,
  type FileRole,
} from './schema'

export type SourceFile = { path: string; file: File }

export type PlannedFile = {
  role: FileRole
  path: string
  file: File
  headers: string[]
  mapping: ColumnMapping
  complete: boolean
}

export type Plan = {
  name: string
  files: Partial<Record<FileRole, PlannedFile>>
  pdfs: SourceFile[]
  ignored: string[]
}

const HEADER_BYTES = 64 * 1024

export async function readHeaders(file: File): Promise<string[]> {
  const head = await file.slice(0, HEADER_BYTES).text()
  const parsed = Papa.parse<string[]>(head.replace(/^﻿/, ''), { preview: 1 })
  return (parsed.data[0] ?? []).map((h) => h.trim())
}

export async function buildPlan(name: string, sources: SourceFile[]): Promise<Plan> {
  const files: Plan['files'] = {}
  const pdfs: SourceFile[] = []
  const ignored: string[] = []

  for (const source of sources) {
    if (source.path.toLowerCase().endsWith('.pdf')) {
      pdfs.push(source)
      continue
    }
    const role = classifyFile(source.path)
    if (!role || files[role]) {
      if (source.path.toLowerCase().endsWith('.csv')) ignored.push(source.path)
      continue
    }
    const headers = await readHeaders(source.file)
    const mapping = resolveColumns(ROLE_SCHEMA[role], headers)
    files[role] = {
      role,
      path: source.path,
      file: source.file,
      headers,
      mapping,
      complete: isCompleteMapping(ROLE_SCHEMA[role], mapping),
    }
  }
  return { name, files, pdfs, ignored }
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
  if (!plan.files.bank) problems.push({ message: 'Falta bank_transactions.csv en la carpeta.' })
  if (!plan.files.ar && !plan.files.ap && !plan.files.cn) {
    problems.push({ message: 'Falta al menos un fichero de partidas: ar_invoices, ap_invoices o credit_notes.' })
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
