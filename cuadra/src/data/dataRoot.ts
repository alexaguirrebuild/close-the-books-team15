import type { SourceFile } from './preflight'

/**
 * The same folder conventions as pipeline/bankrec/roots.py and the engine (bankrec/run.py),
 * so a person picks one folder and gets the phase, the masters and what was written for it.
 */
export const PHASES = ['sample', 'train', 'live', 'final'] as const
export type PhaseName = (typeof PHASES)[number]

export type PhaseLayout = {
  phase: PhaseName
  phaseDir: string
  mastersDir: string | null
  outDir: string
}

const DOCS_DIR = 'bankrec-docs-and-masters'
const DATA_DIR = 'data'
const STATEMENTS = /^bank_transactions\.csv(\.gz)?$/i
const MASTERS_OVERRIDE = 'masters_final'

/** Files in these folders are never read: raw statements and batches stay with the pipeline. */
const SKIPPED_IN_PHASE = new Set(['bank_statements', 'direct_debit_batches', 'card_settlements', 'cheque_deposits', 'payment_runs'])

const parentOf = (path: string) => path.split('/').slice(0, -1).join('/')
const nameOf = (path: string) => path.split('/').at(-1) ?? path

export function detectLayouts(paths: string[]): PhaseLayout[] {
  const folders = new Set(paths.map(parentOf))
  const statements = new Set(paths.filter((path) => STATEMENTS.test(nameOf(path))).map(parentOf))
  const mastersDir = ['masters', `${DATA_DIR}/masters`, `${DOCS_DIR}/masters`].find((dir) => folders.has(dir)) ?? null
  return PHASES.flatMap((phase) => {
    const phaseDir = [phase, `${DATA_DIR}/${phase}`, `${DOCS_DIR}/${phase}`].find((dir) => statements.has(dir))
    return phaseDir ? [{ phase, phaseDir, mastersDir, outDir: `out/${phase}` }] : []
  })
}

const isInside = (path: string, dir: string | null) => dir !== null && (path === dir || path.startsWith(`${dir}/`))

/** The engine writes one pair of files per phase directly under out/, next to its temporary files. */
const engineOutput = (phase: PhaseName) => new RegExp(String.raw`^out/(sub|explain)_${phase}\.csv(\.gz)?$`, 'i')

const ENGINE_FILE_NAME = /^(?:sub|explain)_(sample|train|holdout|live|final)(?=[._]|$)/i

/** The phase an engine file was written for, from its name. The holdout is a part of TRAIN. */
export function phaseOfEngineFile(name: string): PhaseName | null {
  const named = ENGINE_FILE_NAME.exec(name)?.[1].toLowerCase()
  return named === 'holdout' ? 'train' : (PHASES.find((phase) => phase === named) ?? null)
}

export function filesOfLayout(layout: PhaseLayout, sources: SourceFile[]): SourceFile[] {
  const override = `${layout.phaseDir}/${MASTERS_OVERRIDE}`
  const rank = (source: SourceFile) => (isInside(source.path, override) ? 0 : 1)
  const written = engineOutput(layout.phase)
  return sources
    .filter((source) => written.test(source.path) || [layout.phaseDir, layout.mastersDir, layout.outDir].some((dir) => isInside(source.path, dir)))
    .map((source, index) => ({ source, index }))
    .sort((a, b) => rank(a.source) - rank(b.source) || a.index - b.index)
    .map(({ source }) => source)
}

/** Walking only what a phase needs keeps the load fast with tens of thousands of PDFs elsewhere. */
export function shouldEnter(layout: PhaseLayout, dir: string): boolean {
  if (dir.startsWith(`${layout.phaseDir}/`) && SKIPPED_IN_PHASE.has(dir.slice(layout.phaseDir.length + 1).split('/')[0])) {
    return false
  }
  return [layout.phaseDir, layout.mastersDir, layout.outDir].some(
    (root) => root !== null && (isInside(dir, root) || root.startsWith(`${dir}/`)),
  )
}
