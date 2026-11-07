import { describe, expect, test } from 'vitest'
import { ruleLabel, ruleName } from './ruleLabel'

/** The rules the engine wrote on LIVE, without the suffix that says how it found the party. */
const ENGINE_RULES = [
  'm2_avisos', 'remittance', 'ref', 'ref_duplicate', 'ref_represented', 'dd_batch', 'dd_return', 'dd_return_mandate',
  'payment_run_transfer', 'payment_run_e2e', 'payment_run_aggregated', 'payment_run_confirming', 'payment_run_amount',
  'card_settlement', 'card_chargeback', 'cheque_deposit', 'cheque_returned', 'transfer_returned', 'pdf_clause',
  'dispute_settlement', 'reversal_pair', 'fallback_receipt', 'fallback_debit', 'missing', 'ur_represented',
  'party_amount_nombre', 'party_subset_iban', 'party_short_nombre_texto', 'party_fx_nombre_pdf', 'party_partial_nombre_texto',
  'ur_duplicate_nombre', 'kw:interest', 'payment_run', 'remittance_gap', 'party_rescue_exact', 'party_rescue_subset',
]

describe('ruleLabel', () => {
  test('says what an exact rule means', () => {
    expect(ruleLabel('m2_avisos')).toBe('Aviso de pago')
    expect(ruleLabel('fallback_receipt')).toBe('Cobro sin identificar')
  })

  test('groups the rules that only differ in how they found the party', () => {
    expect(ruleLabel('party_amount_nombre')).toBe('Tercero e importe exacto')
    expect(ruleLabel('party_amount_nombre_texto')).toBe('Tercero e importe exacto')
    expect(ruleLabel('ur_duplicate_iban')).toBe('Pago duplicado, por tercero')
  })

  test('hides the keyword pattern behind a plain name', () => {
    expect(ruleLabel('kw:COMISION|COMISIONES|GASTOS|FEE')).toBe('Palabra clave en el concepto')
  })

  test('keeps the labels of the earlier pipeline', () => {
    expect(ruleLabel('m2_remittance')).toBe('Aviso de remesa')
    expect(ruleLabel('baseline')).toBe('Regla básica')
  })

  test('shows an unknown rule readably instead of failing', () => {
    expect(ruleLabel('regla_nueva')).toBe('regla nueva')
  })

  test('tells apart a rule it knows from one it can only spell out', () => {
    expect(ruleName('dd_batch')).toBe('Remesa de recibos')
    expect(ruleName('regla_nueva')).toBeNull()
  })

  test.each(ENGINE_RULES)('has a name for %s', (rule) => {
    expect(ruleLabel(rule)).not.toBe(rule.replace(/_/g, ' '))
  })
})
