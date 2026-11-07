import { describe, expect, test } from 'vitest'
import { availableViews, VIEW_HINT, viewHint } from './queue'

describe('availableViews', () => {
  test('offers every view when there is a golden and the proposals carry confidence', () => {
    expect(availableViews({ hasGolden: true, hasConfidence: true })).toEqual(['priority', 'ready', 'exceptions', 'no_proposal', 'errors', 'done', 'all'])
  })

  test('hides the errors without a golden', () => {
    expect(availableViews({ hasGolden: false, hasConfidence: true })).not.toContain('errors')
  })

  test('hides the list ready to confirm when nothing says how sure a proposal is', () => {
    expect(availableViews({ hasGolden: true, hasConfidence: false })).not.toContain('ready')
  })

  test('offers only the views that need nothing before the data loads', () => {
    expect(availableViews(null)).toEqual(['priority', 'exceptions', 'no_proposal', 'done', 'all'])
  })
})

describe('viewHint', () => {
  test('explains the usual order when there is confidence', () => {
    expect(viewHint('priority', true)).toBe(VIEW_HINT.priority)
  })

  test('does not promise to find the doubtful cases without confidence', () => {
    expect(viewHint('priority', false)).toBe('El motor no da confianza: primero las líneas con más partidas y excepciones raras.')
  })

  test('leaves the other views as they are', () => {
    expect(viewHint('done', false)).toBe(VIEW_HINT.done)
  })

  test('describes the proposal without saying where it came from', () => {
    expect(viewHint('exceptions', true)).toBe('La propuesta trae una excepción: compruébala.')
    expect(viewHint('no_proposal', true)).toBe('No hay propuesta: hay que decidir a mano.')
  })
})
