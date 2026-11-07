import { describe, expect, test } from 'vitest'
import { classifyFile, isCompleteMapping, resolveColumns, SCHEMAS } from './schema'

describe('classifyFile', () => {
  test('recognises official files in any folder', () => {
    expect(classifyFile('train/bank_transactions.csv')).toBe('bank')
    expect(classifyFile('masters/categories.csv')).toBe('categories')
    expect(classifyFile('open_items_at_start.csv')).toBe('open')
    expect(classifyFile('out/propuestas.csv')).toBe('proposals')
  })

  test('accepts golden variants', () => {
    expect(classifyFile('golden_train.csv')).toBe('golden')
    expect(classifyFile('ground_truth.csv')).toBe('golden')
  })

  test('ignores unknown files', () => {
    expect(classifyFile('readme.md')).toBeNull()
    expect(classifyFile('notes.csv')).toBeNull()
  })
})

describe('resolveColumns', () => {
  test('maps aliases regardless of case', () => {
    const headers = ['BANK_TXN_ID', 'booking_date', 'Amount', 'currency', 'account_id', 'description']
    const mapping = resolveColumns('bank', headers)
    expect(mapping).toMatchObject({
      id: 'BANK_TXN_ID',
      date: 'booking_date',
      amount: 'Amount',
      account: 'account_id',
      description: 'description',
    })
  })

  test('leaves unresolved fields empty', () => {
    expect(resolveColumns('bank', ['bank_txn_id']).date).toBeUndefined()
  })

  test('does not reuse one column for two fields', () => {
    const mapping = resolveColumns('invoice', ['item_id', 'number', 'amount', 'open_amount'])
    expect(mapping.amount).toBe('amount')
    expect(mapping.openAmount).toBe('open_amount')
  })
})

describe('isCompleteMapping', () => {
  test('needs every required field', () => {
    const complete = { id: 'a', date: 'b', amount: 'c', currency: 'd', account: 'e', description: 'f' }
    expect(isCompleteMapping('bank', complete)).toBe(true)
    expect(isCompleteMapping('bank', { id: 'a' })).toBe(false)
  })

  test('every schema declares its required fields among its fields', () => {
    for (const schema of Object.values(SCHEMAS)) {
      for (const field of schema.required) expect(schema.fields).toHaveProperty(field)
    }
  })
})
