import { spawnSync } from 'node:child_process'
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, relative } from 'node:path'
import { afterAll, describe, expect, test } from 'vitest'
import { loadCase } from './caseLoader'
import { detectLayouts, filesOfLayout, shouldEnter, type PhaseName } from './dataRoot'
import { buildDiagnosis, segmentReport } from './diagnosis'
import { buildExportRows, validateExport, writeSubmission } from './exportSubmission'
import { ingest } from './ingest'
import { buildPlan, type SourceFile } from './preflight'
import { scoreAgainstGolden } from './stats'
import { createNodeDb } from './testDb'
import { EXCEPTIONS } from '../domain/types'

/**
 * Runs the real data through the real path and the official scorer. The data is not
 * in the repository: BANKREC_DATA_ROOT=/path/to/root npx vitest run src/data/real.test.ts
 */
const ROOT = process.env.BANKREC_DATA_ROOT
const PHASE = (process.env.BANKREC_PHASE ?? 'sample') as PhaseName
const SCORER = 'bankrec-docs-and-masters/starter/bankrec_score.py'

function walk(root: string, enter: (path: string) => boolean, dir = root): SourceFile[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    const path = relative(root, full)
    if (statSync(full).isDirectory()) return !name.startsWith('.') && enter(path) ? walk(root, enter, full) : []
    if (!/\.(csv|csv\.gz|pdf)$/i.test(name)) return []
    return [{ path, open: async () => new File([readFileSync(full)], name, { lastModified: statSync(full).mtimeMs }) }]
  })
}

const scratch = mkdtempSync(join(tmpdir(), 'cuadra-real-'))
afterAll(() => {
  rmSync(scratch, { recursive: true, force: true })
  for (const name of readdirSync('.')) {
    if (/^(tmp_)?out_.*\.csv$/.test(name)) rmSync(name)
  }
})

describe.skipIf(!ROOT)('real data', () => {
  test('loads, proposes candidates, exports and scores like the official scorer', { timeout: 600_000 }, async () => {
    const root = ROOT as string
    const layouts = detectLayouts(walk(root, (path) => !/^(pipeline|cuadra|cache|\.)/.test(path) && path.split('/').length <= 2).map((s) => s.path))
    const layout = layouts.find((l) => l.phase === PHASE)
    expect(layout, `no encuentro la fase ${PHASE}`).toBeDefined()
    if (!layout) return

    const sources = filesOfLayout(layout, walk(root, (path) => shouldEnter(layout, path)))
    const plan = await buildPlan(`real-${PHASE}`, sources, { phase: PHASE })
    expect(plan.files.bank?.compressed).toBe(true)
    expect(plan.files.fx?.complete).toBe(true)
    expect(plan.files.categories?.complete).toBe(true)

    const db = await createNodeDb()
    const started = Date.now()
    const meta = await ingest(db, plan, (progress) => process.stdout.write(`  ${progress.step} a los ${Math.round((Date.now() - started) / 1000)} s\n`))
    process.stdout.write(`${PHASE}: ingesta en ${Date.now() - started} ms, ${meta.txnCount} movimientos, ${meta.itemCount} partidas, propuestas de ${plan.files.proposals?.path}\n`)
    process.stdout.write(`avisos: ${JSON.stringify(meta.warnings)}\n`)
    expect(meta.warnings.join(' ')).not.toContain('no aparecen en los ficheros')
    expect(meta.terms.size).toBeGreaterThan(0)
    expect(meta.rates.length).toBeGreaterThan(1000)

    const [empty] = await db.query(`SELECT count(*) AS n FROM txn WHERE description = ''`)
    // The bank really leaves a handful of lines without text.
    expect(empty.n as number).toBeLessThan(meta.txnCount * 0.001)

    const picked = await db.query('SELECT rid FROM txn USING SAMPLE 40 ROWS')
    let withCandidates = 0
    let totalMs = 0
    for (const row of picked) {
      const begin = Date.now()
      const data = await loadCase(db, meta, row.rid as number)
      totalMs += Date.now() - begin
      if (data.items.size) withCandidates += 1
    }
    process.stdout.write(`casos: ${withCandidates}/40 con candidatas, ${Math.round(totalMs / 40)} ms de media\n`)
    expect(withCandidates).toBeGreaterThan(15)

    const summary = await buildExportRows(db, { includeProposals: true, minConfidence: 0 })
    const issues = await validateExport(db, meta.categories)
    process.stdout.write(`envío: ${JSON.stringify(summary)}\navisos: ${issues.map((i) => `${i.severity} ${i.code} ${i.count}`).join('; ')}\n`)
    expect(issues.filter((i) => i.severity === 'error')).toEqual([])
    expect(summary.txns).toBe(meta.txnCount)

    // Without labels the score means nothing, but the server's validation still runs: a header-only golden is enough.
    const submission = join(scratch, 'submission.csv')
    writeFileSync(submission, await writeSubmission(db))
    writeFileSync(join(scratch, 'exc.json'), JSON.stringify(EXCEPTIONS))
    writeFileSync(join(scratch, 'cats.json'), JSON.stringify(meta.categories))
    writeFileSync(join(scratch, 'no_golden.csv'), 'bank_txn_id,item_id,amount,category,exception,segment,difficulty\n')
    const phaseDir = join(root, layout.phaseDir)
    const goldenPath = plan.files.golden ? join(phaseDir, 'golden_allocations.csv.gz') : join(scratch, 'no_golden.csv')
    const run = spawnSync(
      'python3',
      ['-I', join(root, SCORER), goldenPath, join(phaseDir, 'bank_transactions.csv.gz'), submission, join(scratch, 'exc.json'), join(scratch, 'cats.json')],
      { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 },
    )
    const official = JSON.parse(run.stdout) as { ok: boolean; errors: unknown[]; score: number; metrics: Record<string, number>; segments: Record<string, { n: number; accuracy: number }> }
    process.stdout.write(`oficial: ${official.score} ${JSON.stringify(official.metrics)}\n`)
    expect(official.errors).toEqual([])
    expect(official.ok).toBe(true)
    if (!plan.files.golden) return

    const local = await scoreAgainstGolden(db)
    process.stdout.write(`cuadra: ${local.total}\n`)
    expect(local.total).toBe(official.score)
    expect(local.allocationF1).toBeCloseTo(official.metrics.allocation_f1, 5)
    expect(local.transactionAccuracy).toBeCloseTo(official.metrics.txn_accuracy, 5)
    expect(local.exceptionMacroF1).toBeCloseTo(official.metrics.exception_macro_f1, 5)
    await buildDiagnosis(db)
    const report = await segmentReport(db)
    for (const [segment, value] of Object.entries(official.segments)) {
      expect(local.segments[segment]?.accuracy ?? 0).toBeCloseTo(value.accuracy, 5)
      // The diagnosis has to tell the same story as the scorer, or nobody can trust the Errors view.
      expect(report.find((r) => r.segment === segment)?.accuracy ?? 0).toBeCloseTo(value.accuracy, 5)
    }
  })
})
