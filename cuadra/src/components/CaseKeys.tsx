import type { ReactNode } from 'react'
import type { CaseModel } from './useCaseModel'

const Key = ({ keys, children }: { keys: string[]; children: ReactNode }) => (
  <span>{keys.map((key) => <kbd key={key}>{key}</kbd>)} {children}</span>
)

/** Only what makes sense now: the footer teaches the next step instead of listing the manual. */
export function CaseKeys({ model }: { model: CaseModel }) {
  const { draft, needsException, combos, fifoSwap, docs, visible } = model
  const hasRows = draft.rows.length > 0
  const decided = hasRows || Boolean(draft.category || draft.exception)
  const enterLabel = needsException ? 'elegir excepción' : decided ? 'confirmar y seguir' : null

  return (
    <footer className="case-keys">
      {enterLabel && <Key keys={['Enter']}>{enterLabel}</Key>}
      {visible.length > 0 && <Key keys={['1–9']}>partida</Key>}
      {hasRows && <Key keys={['Tab']}>importe</Key>}
      {hasRows && !needsException && <Key keys={['E']}>excepción</Key>}
      {combos.length > 0 && <Key keys={['A', 'S', 'D']}>combinación</Key>}
      {fifoSwap && <Key keys={['F']}>la más antigua</Key>}
      {!hasRows && <Key keys={['G']}>categoría</Key>}
      {!hasRows && <Key keys={['U']}>sin identificar</Key>}
      <Key keys={['/']}>buscar</Key>
      {docs.length > 0 && <Key keys={['O']}>documento</Key>}
      <Key keys={['J', 'K']}>mover</Key>
      <span className="case-keys-right"><Key keys={['?']}>todos los atajos</Key></span>
    </footer>
  )
}
