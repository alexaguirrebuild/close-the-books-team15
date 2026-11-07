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

  test('recognises compressed files, the way the data is delivered', () => {
    expect(classifyFile('live/bank_transactions.csv.gz')).toBe('bank')
    expect(classifyFile('train/golden_allocations.csv.gz')).toBe('golden')
  })

  test('treats a CSV under the output folder as proposals', () => {
    expect(classifyFile('out/live/submission_m2.csv.gz')).toBe('proposals')
    expect(classifyFile('out/live/matches/m2_remittance.csv.gz')).toBe('proposals')
    expect(classifyFile('out/live/baseline.csv.gz')).toBe('proposals')
  })

  test('still recognises the PDF text index under the output folder', () => {
    expect(classifyFile('out/live/pdfs_text.csv.gz')).toBe('pdfText')
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

  test('reads the columns of the real bank file, with text as the concept', () => {
    const headers = 'bank_txn_id,account_id,entity,bank,statement_format,statement_file,booking_date,value_date,amount,currency,bank_code,bank_subcode,ref1,ref2,text,counterparty_name,counterparty_account,end_to_end_id,remittance_info,orig_amount,orig_currency'.split(',')
    expect(resolveColumns('bank', headers)).toMatchObject({
      id: 'bank_txn_id',
      date: 'booking_date',
      account: 'account_id',
      description: 'text',
      remittance: 'remittance_info',
      counterparty: 'counterparty_name',
      counterpartyAccount: 'counterparty_account',
      endToEnd: 'end_to_end_id',
      entity: 'entity',
      origAmount: 'orig_amount',
      origCurrency: 'orig_currency',
    })
  })

  test('reads the printed number and dates of real invoices', () => {
    const ap = resolveColumns('invoice', 'item_id,entity,supplier_id,supplier_name,supplier_invoice_number,issue_date,due_date,currency,net_amount,tax_amount,total_amount,payment_terms,our_po,payment_method'.split(','))
    expect(ap).toMatchObject({ printed: 'supplier_invoice_number', party: 'supplier_id', partyName: 'supplier_name', issueDate: 'issue_date', dueDate: 'due_date', amount: 'total_amount', terms: 'payment_terms', po: 'our_po' })
  })

  test('reads the open items file as items of their own', () => {
    const open = resolveColumns('open', 'item_id,item_type,entity,party_id,document_number,issue_date,due_date,currency,total_amount,open_amount'.split(','))
    expect(open).toMatchObject({ id: 'item_id', openAmount: 'open_amount', amount: 'total_amount', printed: 'document_number', party: 'party_id', currency: 'currency', issueDate: 'issue_date', dueDate: 'due_date' })
  })

  test('accepts rates given as units per euro', () => {
    const fx = resolveColumns('fx', ['date', 'currency', 'units_per_eur', 'source'])
    expect(fx).toMatchObject({ date: 'date', base: 'currency', rate: 'units_per_eur' })
    expect(fx.quote).toBeUndefined()
    expect(isCompleteMapping('fx', fx)).toBe(true)
  })

  test('reads the early payment discount and its days', () => {
    const terms = resolveColumns('terms', ['code', 'days', 'early_payment_discount_pct', 'discount_days', 'description'])
    expect(terms).toMatchObject({ id: 'code', discount: 'early_payment_discount_pct', discountDays: 'discount_days' })
  })

  test('reads the segment of the golden', () => {
    const golden = resolveColumns('golden', ['bank_txn_id', 'item_id', 'amount', 'category', 'exception', 'segment', 'difficulty'])
    expect(golden).toMatchObject({ segment: 'segment', difficulty: 'difficulty' })
  })

  test('reads the columns every matching stage writes', () => {
    const stage = resolveColumns('proposals', ['bank_txn_id', 'item_id', 'amount', 'category', 'exception', 'source', 'confidence', 'advice_id', 'line_no'])
    expect(stage).toMatchObject({ source: 'source', confidence: 'confidence', adviceId: 'advice_id', lineNo: 'line_no' })
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
