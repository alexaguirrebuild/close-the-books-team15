import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AllocationPanel } from '../components/AllocationPanel'
import { CandidateList } from '../components/CandidateList'
import { CaseHeader } from '../components/CaseHeader'
import { DocsPanel, useCaseDocs } from '../components/DocsPanel'
import { CategoryPalette, ExceptionPalette } from '../components/Palettes'
import { Robot } from '../components/Robot'
import { SearchBox } from '../components/SearchBox'
import { searchPdfs, type CaseData } from '../data/caseLoader'
import { autoAllocate, computeResidual, suggestExceptions } from '../domain/allocation'
import { rankCandidates } from '../domain/candidates'
import { findCombos } from '../domain/combos'
import { draftToDecision, initialDraft, type Draft } from '../domain/draft'
import { convert } from '../domain/money'
import { EXCEPTIONS, type Item } from '../domain/types'
import { useStore } from '../store'

type Overlay = 'none' | 'exception' | 'category' | 'search'

const PDF_URL_LIFETIME_MS = 60_000
const COMBO_KEYS = ['a', 's', 'd']
const MAX_COMBOS = 3

export function Bench() {
  const benchRid = useStore((s) => s.benchRid)
  const benchPos = useStore((s) => s.benchPos)
  const queueTotal = useStore((s) => s.queueTotal)
  const caseFor = useStore((s) => s.caseFor)
  const step = useStore((s) => s.step)
  const go = useStore((s) => s.go)
  const [data, setData] = useState<CaseData | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  useEffect(() => {
    if (benchRid === null) {
      setData(null)
      return
    }
    let current = true
    caseFor(benchRid).then(
      (next) => {
        if (current) {
          setData(next)
          setFailure(null)
        }
      },
      (error: unknown) => setFailure(error instanceof Error ? error.message : String(error)),
    )
    return () => {
      current = false
    }
  }, [benchRid, caseFor])

  if (failure) return <p className="problem page">No se pudo abrir el movimiento: {failure}</p>

  if (benchRid === null || !data) {
    return (
      <div className="bench-empty">
        {benchRid === null ? (
          <>
            <Robot mood="happy" size={72} />
            <h1 className="headline">No queda nada en esta cola.</h1>
            <p className="muted">Cambia los filtros en la cola o valida el envío.</p>
            <div className="team-actions">
              <button className="btn" onClick={() => go('queue')}>Ir a la cola</button>
              <button className="btn" onClick={() => go('export')}>Ir a exportar</button>
              <button className="btn" onClick={() => void step(1)}>Volver a buscar</button>
            </div>
          </>
        ) : (
          <Robot mood="working" size={48} />
        )}
      </div>
    )
  }

  return <BenchCase key={data.txn.rid} data={data} pos={benchPos} total={queueTotal} />
}

type BenchCaseProps = { data: CaseData; pos: number | null; total: number }

function BenchCase({ data, pos, total }: BenchCaseProps) {
  const meta = useStore((s) => s.meta)
  const decide = useStore((s) => s.decide)
  const step = useStore((s) => s.step)
  const usedItems = useStore((s) => s.usedItems)
  const pdfFiles = useStore((s) => s.pdfFiles)
  const notify = useStore((s) => s.notify)
  const db = useStore((s) => s.db)

  const [draft, setDraft] = useState<Draft>(() => initialDraft(data))
  const [overlay, setOverlay] = useState<Overlay>('none')
  const [cursor, setCursor] = useState(0)
  const [extra, setExtra] = useState<Item[]>([])
  const [warned, setWarned] = useState(false)
  const amountRefs = useRef(new Map<string, HTMLInputElement>())

  const rates = meta?.rates ?? []
  const { txn } = data

  const used = useMemo(() => {
    const merged = new Map(data.usedElsewhere)
    usedItems.forEach((txnId, itemId) => {
      if (txnId !== txn.id) merged.set(itemId, txnId)
    })
    return merged
  }, [data.usedElsewhere, usedItems, txn.id])

  const candidates = useMemo(() => {
    const ranked = rankCandidates(txn, data.raw, used)
    const known = new Set(ranked.map((c) => c.item.itemId))
    const searched = extra
      .filter((item) => !known.has(item.itemId))
      .map((item) => ({ item, reasons: ['search' as const], fragment: null, fifo: false, usedBy: used.get(item.itemId) ?? null }))
    return [...searched, ...ranked]
  }, [txn, data.raw, used, extra])

  const items = useMemo(() => {
    const map = new Map(data.items)
    extra.forEach((item) => map.set(item.itemId, item))
    return map
  }, [data.items, extra])

  const combos = useMemo(() => {
    const pool = data.raw.party
      .filter((item) => !used.has(item.itemId))
      .flatMap((item) => {
        const size = Math.abs(item.openAmount)
        const value = convert(item.kind === 'CN' ? -size : size, item.currency, txn.currency, txn.date, rates)
        return value === null ? [] : [{ id: item.itemId, value }]
      })
    return findCombos(pool, Math.abs(txn.amount), { limit: MAX_COMBOS })
  }, [data.raw.party, used, txn, rates])

  const residual = useMemo(() => computeResidual(txn, draft.rows, items, rates), [txn, draft.rows, items, rates])

  const discountPct = useMemo(() => {
    const terms = draft.rows.map((row) => items.get(row.itemId)?.terms ?? '')
    const found = terms.map((t) => meta?.discounts.get(t)).find((d) => d !== undefined)
    return found ?? null
  }, [draft.rows, items, meta])

  const suggested = useMemo(() => {
    if (!draft.rows.length) return []
    return suggestExceptions({
      residual: residual.residual,
      balanced: residual.balanced,
      fxInvolved: residual.fxInvolved,
      txnAmount: txn.amount,
      discountPct,
      openGap: residual.openGap,
    })
  }, [draft.rows.length, residual, txn.amount, discountPct])

  const fifoSwap = useMemo(() => {
    if (draft.rows.length !== 1) return null
    const selected = candidates.find((c) => c.item.itemId === draft.rows[0].itemId)
    if (!selected || selected.fifo || selected.reasons.includes('printed')) return null
    return (
      candidates.find(
        (c) =>
          c.fifo &&
          c.item.partyId === selected.item.partyId &&
          c.item.currency === selected.item.currency &&
          c.item.openAmount === selected.item.openAmount,
      ) ?? null
    )
  }, [draft.rows, candidates])

  const docs = useCaseDocs(db, data, candidates, searchPdfs)

  const setRows = useCallback(
    (selected: Item[]) => {
      setDraft((current) => ({
        ...current,
        rows: autoAllocate(selected, txn, rates),
        category: selected.length ? null : current.category,
        touched: true,
      }))
      setWarned(false)
    },
    [txn, rates],
  )

  const toggle = useCallback(
    (item: Item) => {
      const selectedIds = draft.rows.map((r) => r.itemId)
      const nextIds = selectedIds.includes(item.itemId)
        ? selectedIds.filter((id) => id !== item.itemId)
        : [...selectedIds, item.itemId]
      const selected = nextIds.flatMap((id) => {
        const found = id === item.itemId ? item : items.get(id)
        return found ? [found] : []
      })
      setRows(selected)
    },
    [draft.rows, items, setRows],
  )

  const applyCombo = useCallback(
    (index: number) => {
      const combo = combos[index]
      if (!combo) return
      setRows(combo.ids.flatMap((id) => items.get(id) ?? []))
    },
    [combos, items, setRows],
  )

  const save = useCallback(() => {
    const decision = draftToDecision(txn.id, draft, Date.now())
    if (!decision) {
      notify('ok', 'Elige una partida, una excepción o una category antes de confirmar.')
      return
    }
    if (suggested.length && !draft.exception && !warned) {
      setWarned(true)
      return
    }
    decide(txn.rid, decision)
    void step(1)
  }, [txn, draft, suggested, warned, decide, step, notify])

  const markUnidentified = useCallback(() => {
    decide(txn.rid, { txnId: txn.id, allocations: [], category: null, exception: 'UNIDENTIFIED', source: 'manual', at: Date.now() })
    void step(1)
  }, [txn, decide, step])

  const pickException = (code: Draft['exception']) => {
    setDraft((current) => ({ ...current, exception: current.exception === code ? null : code, touched: true }))
    setOverlay('none')
    setWarned(false)
  }

  const pickCategory = (category: string) => {
    setDraft((current) => ({ ...current, category, rows: [], exception: null, touched: true }))
    setOverlay('none')
  }

  const openDoc = useCallback(
    (path: string) => {
      const file = pdfFiles.find((f) => f.path === path || f.path.endsWith(path))
      if (!file) {
        notify('error', `${path} no está en la carpeta cargada.`)
        return
      }
      const url = URL.createObjectURL(file.file)
      window.open(url, '_blank', 'noopener')
      setTimeout(() => URL.revokeObjectURL(url), PDF_URL_LIFETIME_MS)
    },
    [pdfFiles, notify],
  )

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (overlay !== 'none' || event.altKey || event.metaKey || event.ctrlKey) return
      if (useStore.getState().helpOpen) return
      const target = event.target
      const amounts = [...amountRefs.current.values()]
      if (target instanceof HTMLInputElement || target instanceof HTMLSelectElement) {
        if (event.key === 'Enter') {
          event.preventDefault()
          target.blur()
          save()
        } else if (event.key === 'Escape') {
          target.blur()
        } else if (event.key === 'Tab' && target instanceof HTMLInputElement && amounts.includes(target)) {
          event.preventDefault()
          const delta = event.shiftKey ? -1 : 1
          amounts[(amounts.indexOf(target) + delta + amounts.length) % amounts.length]?.focus()
        }
        return
      }
      if (event.key === 'Tab' && amounts.length) {
        event.preventDefault()
        amounts[0].focus()
        return
      }
      const key = event.key.toLowerCase()
      if (key === 'enter') {
        event.preventDefault()
        save()
      } else if (/^[1-9]$/.test(key)) {
        const candidate = candidates[Number(key) - 1]
        if (candidate) toggle(candidate.item)
      } else if (key === 'arrowdown') {
        event.preventDefault()
        setCursor((c) => Math.min(c + 1, candidates.length - 1))
      } else if (key === 'arrowup') {
        event.preventDefault()
        setCursor((c) => Math.max(c - 1, 0))
      } else if (key === ' ') {
        event.preventDefault()
        const candidate = candidates[cursor]
        if (candidate) toggle(candidate.item)
      } else if (COMBO_KEYS.includes(key)) {
        applyCombo(COMBO_KEYS.indexOf(key))
      } else if (key === 'f' && fifoSwap) {
        toggleToFifo(fifoSwap.item)
      } else if (key === 'e') {
        event.preventDefault()
        setOverlay('exception')
      } else if (key === 'g') {
        event.preventDefault()
        setOverlay('category')
      } else if (key === 'u') {
        markUnidentified()
      } else if (key === '/') {
        event.preventDefault()
        setOverlay('search')
      } else if (key === 'o') {
        if (docs[0]) openDoc(docs[0].file)
      } else if (key === 'j' || key === 'arrowright') {
        void step(1)
      } else if (key === 'k' || key === 'arrowleft') {
        void step(-1)
      }
    }
    const toggleToFifo = (item: Item) => setRows([item])
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [overlay, save, candidates, cursor, toggle, applyCombo, fifoSwap, markUnidentified, step, docs, openDoc, setRows])

  const editAmount = (itemId: string, amount: number) => {
    setDraft((current) => ({
      ...current,
      touched: true,
      rows: current.rows.map((row) => (row.itemId === itemId ? { ...row, amount } : row)),
    }))
    setWarned(false)
  }

  return (
    <div className="bench">
      <CaseHeader data={data} pos={pos} total={total} />
      <div className="bench-body">
        <CandidateList
          candidates={candidates}
          combos={combos}
          comboKeys={COMBO_KEYS}
          selectedIds={draft.rows.map((row) => row.itemId)}
          cursor={cursor}
          txn={txn}
          onToggle={toggle}
          onApplyCombo={applyCombo}
          onSearch={() => setOverlay('search')}
        />
        <div className="bench-side">
          <AllocationPanel
            draft={draft}
            data={data}
            items={items}
            residual={residual}
            warned={warned}
            suggested={suggested}
            fifoSwap={fifoSwap?.item ?? null}
            amountRefs={amountRefs}
            onEditAmount={editAmount}
            onOpenExceptions={() => setOverlay('exception')}
            onOpenCategories={() => setOverlay('category')}
            onClearException={() => pickException(draft.exception)}
            onSave={save}
          />
          <DocsPanel docs={docs} onOpen={openDoc} />
        </div>
      </div>
      <footer className="bench-keys">
        <span><kbd>Enter</kbd> confirmar y seguir</span>
        <span><kbd>1</kbd>–<kbd>9</kbd> partida</span>
        <span><kbd>Tab</kbd> importe</span>
        <span><kbd>E</kbd> excepción</span>
        <span><kbd>G</kbd> category</span>
        <span><kbd>U</kbd> sin identificar</span>
        <span><kbd>/</kbd> buscar</span>
        <span><kbd>J</kbd><kbd>K</kbd> saltar</span>
        <span><kbd>Z</kbd> deshacer</span>
        <SessionPace />
      </footer>
      {overlay === 'exception' && (
        <ExceptionPalette
          codes={EXCEPTIONS}
          current={draft.exception}
          suggested={suggested}
          onPick={pickException}
          onClose={() => setOverlay('none')}
        />
      )}
      {overlay === 'category' && (
        <CategoryPalette categories={meta?.categories ?? []} current={draft.category} onPick={pickCategory} onClose={() => setOverlay('none')} />
      )}
      {overlay === 'search' && (
        <SearchBox
          used={used}
          onPick={(item) => {
            setExtra((current) => [item, ...current.filter((i) => i.itemId !== item.itemId)])
            toggle(item)
            setOverlay('none')
          }}
          onClose={() => setOverlay('none')}
        />
      )}
    </div>
  )
}

const PACE_WINDOW_MS = 5 * 60 * 1000

function SessionPace() {
  const times = useStore((s) => s.sessionTimes)
  const recent = times.filter((t) => Date.now() - t < PACE_WINDOW_MS)
  if (recent.length < 2) return <span className="pace">{times.length} en esta sesión</span>
  const minutes = Math.max((Date.now() - recent[0]) / 60000, 1 / 60)
  return <span className="pace">{times.length} en esta sesión, {Math.round(recent.length / minutes)} por minuto</span>
}
