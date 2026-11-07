import type { BalanceStatus } from './balance'
import type { PrintedToken } from './printed'
import { ruleLabel, ruleName } from './ruleLabel'
import type { ProposalRow } from './types'

export type EvidenceState = 'ok' | 'warn' | 'bad' | 'none'
export type EvidenceStep = { id: string; label: string; state: EvidenceState; text: string }

export type EvidenceInput = {
  tokens: PrintedToken[]
  matchedFragments: string[]
  partyName: string | null
  counterparty: string
  proposal: ProposalRow[]
  balanceTitle: string
  balanceStatus: BalanceStatus
  documents: number
  golden: { ok: boolean; text: string } | null
}

function reference({ tokens, matchedFragments }: EvidenceInput): EvidenceStep {
  const base = { id: 'reference', label: 'Referencia' }
  const hit = tokens.find((token) => matchedFragments.includes(token.norm))
  if (hit) return { ...base, state: 'ok', text: `${hit.text} aparece en el concepto` }
  if (tokens.length) return { ...base, state: 'warn', text: `El concepto trae ${tokens[0].text}, sin partida con ese número` }
  return { ...base, state: 'none', text: 'El concepto no trae ningún número' }
}

function party({ partyName, counterparty }: EvidenceInput): EvidenceStep {
  const base = { id: 'party', label: 'Tercero' }
  if (!partyName) return counterparty ? { ...base, state: 'warn', text: `Ordenante: ${counterparty}` } : { ...base, state: 'warn', text: 'Sin identificar' }
  const different = counterparty && counterparty.toUpperCase() !== partyName.toUpperCase()
  return { ...base, state: 'ok', text: different ? `${partyName}. Ordenante: ${counterparty}` : partyName }
}

/** Where the proposal comes from: its reason if it has one, then the advice behind it, then its rule. */
function origin({ proposal }: EvidenceInput): EvidenceStep {
  const base = { id: 'origin', label: 'De dónde sale' }
  if (!proposal.length) return { ...base, state: 'warn', text: 'No hay propuesta' }
  const withSource = proposal.find((row) => row.adviceId || row.source || row.reason) ?? proposal[0]
  if (withSource.reason) {
    const name = ruleName(withSource.source)
    return { ...base, state: 'none', text: name ? `${name}: ${withSource.reason}` : withSource.reason }
  }
  if (withSource.adviceId) {
    const line = withSource.lineNo ? `, línea ${withSource.lineNo}` : ''
    return { ...base, state: 'none', text: `${ruleLabel(withSource.source)} ${withSource.adviceId}${line}` }
  }
  return { ...base, state: 'none', text: withSource.source ? ruleLabel(withSource.source) : 'Propuesta del pipeline, sin detalle' }
}

const AMOUNT_OK: BalanceStatus[] = ['balanced', 'explained', 'no_item']

/** The reasoning behind a proposal as a short trace, so trusting it takes one glance. */
export function buildEvidence(input: EvidenceInput): EvidenceStep[] {
  const amount: EvidenceStep = {
    id: 'amount',
    label: 'Importe',
    state: AMOUNT_OK.includes(input.balanceStatus) ? 'ok' : input.balanceStatus === 'empty' ? 'none' : 'warn',
    text: input.balanceTitle,
  }
  const documents: EvidenceStep[] = input.documents
    ? [{ id: 'documents', label: 'Documentos', state: 'ok', text: `${input.documents} ${input.documents === 1 ? 'documento lo menciona' : 'documentos lo mencionan'}` }]
    : []
  const golden: EvidenceStep[] = input.golden
    ? [{ id: 'golden', label: 'Golden', state: input.golden.ok ? 'ok' : 'bad', text: input.golden.text }]
    : []
  return [reference(input), party(input), amount, origin(input), ...documents, ...golden]
}
