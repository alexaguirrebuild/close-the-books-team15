import { describe, expect, test } from 'vitest'
import { detectLayouts, filesOfLayout, phaseOfEngineFile, shouldEnter, type PhaseLayout } from './dataRoot'

const live: PhaseLayout = { phase: 'live', phaseDir: 'live', mastersDir: 'bankrec-docs-and-masters/masters', outDir: 'out/live' }

describe('detectLayouts', () => {
  test('finds the phases next to the documentation folder, like the pipeline does', () => {
    const layouts = detectLayouts([
      'live/bank_transactions.csv.gz',
      'train/bank_transactions.csv.gz',
      'bankrec-docs-and-masters/sample/bank_transactions.csv.gz',
      'bankrec-docs-and-masters/masters/categories.csv',
    ])
    expect(layouts.map((l) => l.phase)).toEqual(['sample', 'train', 'live'])
    expect(layouts.find((l) => l.phase === 'sample')?.phaseDir).toBe('bankrec-docs-and-masters/sample')
    expect(layouts.every((l) => l.mastersDir === 'bankrec-docs-and-masters/masters')).toBe(true)
  })

  test('prefers masters at the root', () => {
    const [layout] = detectLayouts(['live/bank_transactions.csv.gz', 'masters/fx_rates.csv'])
    expect(layout.mastersDir).toBe('masters')
  })

  test('accepts plain CSV', () => {
    expect(detectLayouts(['final/bank_transactions.csv']).map((l) => l.phase)).toEqual(['final'])
  })

  test('reports a phase without masters so the screen can ask for them', () => {
    expect(detectLayouts(['live/bank_transactions.csv.gz'])[0].mastersDir).toBeNull()
  })

  test('finds nothing in a folder that is itself a dataset', () => {
    expect(detectLayouts(['bank_transactions.csv.gz', 'masters/fx_rates.csv'])).toEqual([])
  })

  test('finds the phases and the masters inside data/, where the engine keeps them', () => {
    const layouts = detectLayouts([
      'data/live/bank_transactions.csv.gz',
      'data/sample/bank_transactions.csv.gz',
      'data/masters/fx_rates.csv',
    ])
    expect(layouts.map((l) => l.phase)).toEqual(['sample', 'live'])
    expect(layouts[1]).toEqual({ phase: 'live', phaseDir: 'data/live', mastersDir: 'data/masters', outDir: 'out/live' })
  })
})

describe('filesOfLayout', () => {
  const source = (path: string) => ({ path, open: async () => new File([], path) })

  test('keeps the phase, the masters and its output, and nothing from other phases', () => {
    const paths = filesOfLayout(live, [
      source('train/bank_transactions.csv.gz'),
      source('live/bank_transactions.csv.gz'),
      source('bankrec-docs-and-masters/masters/categories.csv'),
      source('out/live/submission_m2.csv.gz'),
      source('out/train/baseline.csv.gz'),
    ]).map((s) => s.path)
    expect(paths).toEqual([
      'live/bank_transactions.csv.gz',
      'bankrec-docs-and-masters/masters/categories.csv',
      'out/live/submission_m2.csv.gz',
    ])
  })

  test('keeps what the engine writes for this phase directly under out/, and nothing else from there', () => {
    const paths = filesOfLayout(live, [
      source('out/explain_live.csv.gz'),
      source('out/sub_live.csv.gz'),
      source('out/sub_train.csv.gz'),
      source('out/explain_holdout.csv.gz'),
      source('out/_txns.csv.gz'),
      source('out/_golden_holdout.csv.gz'),
    ]).map((s) => s.path)
    expect(paths).toEqual(['out/explain_live.csv.gz', 'out/sub_live.csv.gz'])
  })

  test('puts the masters of FINAL before the general ones, because they replace them', () => {
    const final: PhaseLayout = { ...live, phase: 'final', phaseDir: 'final', outDir: 'out/final' }
    const paths = filesOfLayout(final, [
      source('bankrec-docs-and-masters/masters/fx_rates.csv'),
      source('final/masters_final/fx_rates.csv'),
    ]).map((s) => s.path)
    expect(paths).toEqual(['final/masters_final/fx_rates.csv', 'bankrec-docs-and-masters/masters/fx_rates.csv'])
  })
})

describe('phaseOfEngineFile', () => {
  test('reads the phase from the name the engine gives its files', () => {
    expect(phaseOfEngineFile('explain_live.csv.gz')).toBe('live')
    expect(phaseOfEngineFile('sub_final.csv')).toBe('final')
  })

  test('accepts a copy with a suffix, such as a saved submission', () => {
    expect(phaseOfEngineFile('sub_live_648cc9c.csv.gz')).toBe('live')
    expect(phaseOfEngineFile('sub_train_since2025-01-01.csv.gz')).toBe('train')
  })

  test('treats the holdout as TRAIN, because its rows come from there', () => {
    expect(phaseOfEngineFile('explain_holdout.csv.gz')).toBe('train')
  })

  test('says nothing about any other name', () => {
    expect(phaseOfEngineFile('mi_revision.csv')).toBeNull()
    expect(phaseOfEngineFile('sub_lively.csv')).toBeNull()
  })
})

describe('shouldEnter', () => {
  test('walks towards the folders of the layout and through them', () => {
    expect(shouldEnter(live, 'live')).toBe(true)
    expect(shouldEnter(live, 'live/pdfs/ES01')).toBe(true)
    expect(shouldEnter(live, 'bankrec-docs-and-masters')).toBe(true)
    expect(shouldEnter(live, 'out')).toBe(true)
    expect(shouldEnter(live, 'out/live/matches')).toBe(true)
  })

  test('skips other phases and folders with nothing to load', () => {
    expect(shouldEnter(live, 'train')).toBe(false)
    expect(shouldEnter(live, 'out/train')).toBe(false)
    expect(shouldEnter(live, 'live/bank_statements')).toBe(false)
    expect(shouldEnter(live, 'bankrec-docs-and-masters/starter')).toBe(false)
  })
})
