const EXACT: Record<string, string> = {
  m2_remittance: 'Aviso de remesa',
  baseline: 'Regla básica',
  m2_avisos: 'Aviso de pago',
  remittance: 'Aviso de pago',
  remittance_gap: 'Aviso de pago con diferencia',
  payment_run: 'Remesa de pagos',
  party_rescue_exact: 'Tercero rescatado por importe exacto',
  party_rescue_subset: 'Tercero rescatado por combinación de partidas',
  ref: 'Referencia en el concepto',
  ref_duplicate: 'Pago duplicado, por referencia',
  ref_represented: 'Recibo devuelto y pagado otra vez',
  ur_represented: 'Recibo devuelto y pagado otra vez',
  dd_batch: 'Remesa de recibos',
  dd_return: 'Recibo devuelto',
  dd_return_mandate: 'Recibo devuelto',
  payment_run_confirming: 'Confirming',
  card_settlement: 'Liquidación de tarjetas',
  card_chargeback: 'Retrocesión de tarjeta',
  cheque_deposit: 'Remesa de cheques',
  cheque_returned: 'Cheque devuelto',
  transfer_returned: 'Transferencia devuelta',
  pdf_clause: 'Cláusula del PDF',
  dispute_settlement: 'Liquidación de un importe discutido',
  reversal_pair: 'Retrocesión del banco',
  fallback_receipt: 'Cobro sin identificar',
  fallback_debit: 'Cargo sin regla: comisión bancaria',
  missing: 'Sin regla: queda sin identificar',
}

/** Rules that only differ in how they found the party (name, IBAN, text, PDF) or in the kind of run. */
const FAMILIES: [string, string][] = [
  ['kw:', 'Palabra clave en el concepto'],
  ['payment_run_', 'Remesa de pagos'],
  ['party_amount_', 'Tercero e importe exacto'],
  ['party_subset_', 'Tercero y combinación de partidas'],
  ['party_short_', 'Tercero y pago corto'],
  ['party_fx_', 'Tercero y cambio de divisa'],
  ['party_partial_', 'Tercero y pago parcial'],
  ['ur_duplicate_', 'Pago duplicado, por tercero'],
]

/** Says in words why the engine or the pipeline proposed a row, when the rule is a known one. */
export function ruleName(rule: string): string | null {
  const exact = Object.hasOwn(EXACT, rule) ? EXACT[rule] : undefined
  return exact ?? FAMILIES.find(([prefix]) => rule.startsWith(prefix))?.[1] ?? null
}

/** The readable name of an engine rule. An unknown rule keeps its own name, with spaces instead of underscores. */
export function ruleLabel(rule: string): string {
  return ruleName(rule) ?? rule.replace(/_/g, ' ')
}
