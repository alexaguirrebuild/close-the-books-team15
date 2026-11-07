import { ExternalLink, X } from 'lucide-react'
import type { CaseData } from '../data/caseLoader'
import { EXCEPTIONS } from '../domain/types'
import { useStore } from '../store'
import { CandidateList } from './CandidateList'
import { CaseHeader } from './CaseHeader'
import { CaseKeys } from './CaseKeys'
import { DecisionPanel } from './DecisionPanel'
import { CategoryPalette, ExceptionPalette } from './Palettes'
import { SearchBox } from './SearchBox'
import { useCaseKeys } from './useCaseKeys'
import { useCaseModel, type CaseModel } from './useCaseModel'

type CaseDetailProps = { data: CaseData; pos: number | null; total: number; isLoadingNext: boolean }

export function CaseDetail({ data, pos, total, isLoadingNext }: CaseDetailProps) {
  const model = useCaseModel(data)
  useCaseKeys(model)

  return (
    <article className={isLoadingNext ? 'case is-loading-next' : 'case'} aria-busy={isLoadingNext}>
      <CaseHeader model={model} pos={pos} total={total} />
      <div className="case-body">
        {model.pdf ? <PdfPane model={model} /> : <CandidateList model={model} />}
        <DecisionPanel model={model} />
      </div>
      <CaseKeys model={model} />
      <CaseOverlays model={model} />
    </article>
  )
}

function PdfPane({ model }: { model: CaseModel }) {
  const pdf = model.pdf
  if (!pdf) return null
  return (
    <section className="pdf-pane" aria-label="Documento">
      <div className="pdf-head">
        <span className="id">{pdf.name}</span>
        <span className="case-empty-actions">
          <a className="btn" href={pdf.url} target="_blank" rel="noopener noreferrer">
            <ExternalLink size={14} aria-hidden="true" /> Abrir aparte
          </a>
          <button className="btn" onClick={model.closeDoc}>
            <X size={14} aria-hidden="true" /> Cerrar <kbd>O</kbd>
          </button>
        </span>
      </div>
      <iframe src={pdf.url} title={pdf.name} />
    </section>
  )
}

function CaseOverlays({ model }: { model: CaseModel }) {
  const meta = useStore((s) => s.meta)
  const { overlay, setOverlay, draft, exception, suggestions, items, used } = model
  const close = () => setOverlay({ kind: 'none' })

  if (overlay.kind === 'exception') {
    const row = overlay.itemId ? draft.rows.find((r) => r.itemId === overlay.itemId) : null
    const printed = overlay.itemId ? (items.get(overlay.itemId)?.printed || overlay.itemId) : null
    return (
      <ExceptionPalette
        title={printed ? `Excepción de ${printed}` : 'Excepción del movimiento'}
        codes={EXCEPTIONS}
        current={row ? row.exception : exception}
        suggestions={suggestions}
        onPick={(code) => model.pickException(code, overlay.itemId)}
        onClose={close}
      />
    )
  }
  if (overlay.kind === 'category') {
    return (
      <CategoryPalette
        categories={meta?.categories ?? []}
        labels={meta?.categoryLabels ?? new Map()}
        current={draft.category}
        onPick={model.pickCategory}
        onClose={close}
      />
    )
  }
  if (overlay.kind === 'search') {
    return <SearchBox used={used} onPick={model.addSearched} onClose={close} />
  }
  return null
}
