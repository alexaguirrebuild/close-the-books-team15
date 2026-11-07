import { describe, expect, test } from 'vitest'
import { buildEvidence, type EvidenceInput } from './evidence'

const input = (over: Partial<EvidenceInput> = {}): EvidenceInput => ({
  tokens: [],
  matchedFragments: [],
  partyName: null,
  counterparty: '',
  proposal: [],
  balanceTitle: 'Cuadra exacto',
  balanceStatus: 'balanced',
  documents: 0,
  golden: null,
  ...over,
})

const row = { itemId: 'A', amount: 1, exception: null, category: null, confidence: 0.8, reason: '', source: '', adviceId: null, lineNo: null }
const step = (id: string, over: Partial<EvidenceInput>) => buildEvidence(input(over)).find((s) => s.id === id)

describe('buildEvidence', () => {
  test('says the number was found in the concept', () => {
    const tokens = [{ text: 'FV25-1', start: 0, end: 6, norm: 'FV251', variants: ['FV251'] }]
    expect(step('reference', { tokens, matchedFragments: ['FV251'] })).toMatchObject({ state: 'ok', text: 'FV25-1 aparece en el concepto' })
  })

  test('warns when the concept has a number that matches no item', () => {
    const tokens = [{ text: '12345', start: 0, end: 5, norm: '12345', variants: ['12345'] }]
    expect(step('reference', { tokens })).toMatchObject({ state: 'warn', text: 'El concepto trae 12345, sin partida con ese número' })
  })

  test('is neutral when the concept has no number', () => {
    expect(step('reference', {})).toMatchObject({ state: 'none', text: 'El concepto no trae ningún número' })
  })

  test('names the party and who paid when they differ', () => {
    expect(step('party', { partyName: 'Riegos Oasis', counterparty: 'Banco Factor' })).toMatchObject({ state: 'ok', text: 'Riegos Oasis. Ordenante: Banco Factor' })
  })

  test('warns when no party is known', () => {
    expect(step('party', {})).toMatchObject({ state: 'warn', text: 'Sin identificar' })
  })

  test('repeats what the balance says about the amount', () => {
    expect(step('amount', {})).toMatchObject({ state: 'ok', text: 'Cuadra exacto' })
    expect(step('amount', { balanceStatus: 'over', balanceTitle: 'Se pasan' })).toMatchObject({ state: 'warn', text: 'Se pasan' })
  })

  test('explains a remittance advice from the source and its line', () => {
    const proposal = [{ ...row, source: 'm2_remittance', adviceId: 'RA-7', lineNo: '3' }]
    expect(step('origin', { proposal })?.text).toBe('Aviso de remesa RA-7, línea 3')
  })

  test('shows the reason the pipeline gave', () => {
    expect(step('origin', { proposal: [{ ...row, reason: 'Mismo tercero' }] })?.text).toBe('Mismo tercero')
  })

  test('says when the pipeline proposes nothing', () => {
    expect(step('origin', {})).toMatchObject({ state: 'warn', text: 'El pipeline no propone nada' })
  })

  test('leaves out documents when there are none', () => {
    expect(step('documents', {})).toBeUndefined()
    expect(step('documents', { documents: 2 })?.text).toBe('2 documentos lo mencionan')
  })

  test('adds the golden only when it is known', () => {
    expect(step('golden', {})).toBeUndefined()
    expect(step('golden', { golden: { ok: false, text: 'Partida errónea' } })).toMatchObject({ state: 'bad', text: 'Partida errónea' })
  })
})
