import { ArrowUpRight, Check } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Robot } from '../components/Robot'
import { readFolderHandle } from '../data/log'
import { buildPlan, planProblems, withMapping, type Plan, type SourceFile } from '../data/preflight'
import { ROLE_LABEL, ROLE_SCHEMA, SCHEMAS, type FileRole } from '../data/schema'
import { canPickDirectory, fetchSample, pickDirectory, reopenDirectory, sourcesFromInput } from '../data/sources'
import { formatCount } from '../domain/money'
import { useStore } from '../store'

const SLOTS: FileRole[] = ['bank', 'ar', 'ap', 'cn', 'open', 'proposals', 'golden', 'categories', 'customers', 'suppliers', 'fx', 'terms', 'pdfText']
const REQUIRED: FileRole[] = ['bank']

export function LoadScreen() {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastFolder, setLastFolder] = useState<FileSystemDirectoryHandle | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const progress = useStore((s) => s.progress)
  const loadError = useStore((s) => s.loadError)
  const loadDataset = useStore((s) => s.loadDataset)

  useEffect(() => {
    readFolderHandle().then(setLastFolder, () => setLastFolder(null))
  }, [])

  const accept = useCallback(async (name: string, sources: SourceFile[]) => {
    setError(null)
    setPlan(await buildPlan(name, sources))
  }, [])

  const run = useCallback(
    async (open: () => Promise<{ name: string; sources: SourceFile[] } | null>) => {
      try {
        const picked = await open()
        if (picked) await accept(picked.name, picked.sources)
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    },
    [accept],
  )

  const choose = () => {
    if (canPickDirectory()) void run(pickDirectory)
    else inputRef.current?.click()
  }

  const problems = plan ? planProblems(plan) : []
  const ready = Boolean(plan) && problems.length === 0 && !progress

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const sample = params.get('demo')
    if (!sample) return
    void (async () => {
      const picked = await fetchSample(sample)
      const built = await buildPlan(picked.name, picked.sources)
      setPlan(built)
      if (params.has('autoload')) await loadDataset(built)
    })()
  }, [loadDataset])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Enter' && ready && plan && !(event.target instanceof HTMLSelectElement)) {
        event.preventDefault()
        void loadDataset(plan)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [ready, plan, loadDataset])

  return (
    <div className="load">
      <div className="load-bg" aria-hidden="true" />
      <section className="load-hero">
        <h1 className="display">Cuadra cada movimiento.</h1>
        <div className="folder-control">
          <div className="folder-path">{plan ? plan.name : 'Carpeta con los CSV descargados'}</div>
          <button className="cta cta-large" onClick={choose}>
            Elegir carpeta
            <span className="cta-box">
              <ArrowUpRight size={14} strokeWidth={1.5} />
            </span>
          </button>
          <input
            ref={inputRef}
            type="file"
            hidden
            multiple
            {...({ webkitdirectory: '' } as object)}
            onChange={(event) => {
              if (event.target.files?.length) {
                const picked = sourcesFromInput(event.target.files)
                void accept(picked.name, picked.sources)
              }
            }}
          />
        </div>
        <p className="lede">
          Revisa las propuestas del pipeline por teclado, corrige lo dudoso y exporta un CSV de envío validado. Todo se
          queda en este navegador.
        </p>
        <div className="load-links">
          {lastFolder && (
            <button className="link" onClick={() => void run(() => reopenDirectory(lastFolder))}>
              Reabrir {lastFolder.name}
            </button>
          )}
          <button className="link" onClick={() => void run(() => fetchSample('train'))}>
            Probar con datos inventados, con golden
          </button>
          <button className="link" onClick={() => void run(() => fetchSample('live'))}>
            Probar con datos inventados, sin golden
          </button>
        </div>

        {(error || loadError) && (
          <p className="problem" role="alert">
            No se pudo leer la carpeta: {error ?? loadError}
          </p>
        )}

        {plan && (
          <div className="load-actions">
            {problems.map((problem) => (
              <p key={problem.message} className="problem">
                {problem.message}
              </p>
            ))}
            {progress ? (
              <div className="progress" role="status">
                <Robot mood="working" size={32} />
                <div className="progress-text">
                  <span>{progress.step}</span>
                  <div className="meter" aria-hidden="true">
                    <div style={{ width: `${(progress.done / progress.total) * 100}%` }} />
                  </div>
                </div>
              </div>
            ) : (
              <button className="cta cta-large" disabled={!ready} onClick={() => plan && void loadDataset(plan)}>
                Cargar datos
                <span className="cta-box">
                  <kbd className="kbd-invert">Enter</kbd>
                </span>
              </button>
            )}
          </div>
        )}
      </section>

      <section className="file-grid" aria-label="Ficheros detectados">
        {SLOTS.map((role) => {
          const file = plan?.files[role]
          const missing = !file && REQUIRED.includes(role)
          return (
            <div key={role} className={`file-cell${file ? ' is-found' : ''}${missing && plan ? ' is-missing' : ''}`}>
              <span className="file-name">{ROLE_LABEL[role].replace(/([_/])/g, '$1\u200b')}</span>
              {file ? (
                <span className="file-meta">
                  <Check size={12} aria-label="Encontrado" /> {formatSize(file.file.size)}
                </span>
              ) : (
                <span className="file-meta">{plan ? 'no está' : ''}</span>
              )}
              {file && !file.complete && plan && (
                <MappingEditor plan={plan} role={role} onChange={setPlan} />
              )}
            </div>
          )
        })}
        <div className={`file-cell${plan?.pdfs.length ? ' is-found' : ''}`}>
          <span className="file-name">pdfs/</span>
          <span className="file-meta">{plan ? `${formatCount(plan.pdfs.length)} PDF` : ''}</span>
        </div>
        <div className="file-cell file-cell-robot">
          <Robot mood={plan ? (problems.length ? 'worried' : 'happy') : 'neutral'} size={56} />
        </div>
      </section>
    </div>
  )
}

function formatSize(bytes: number): string {
  if (bytes > 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes > 1e6) return `${(bytes / 1e6).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`
}

type MappingEditorProps = { plan: Plan; role: FileRole; onChange: (plan: Plan) => void }

function MappingEditor({ plan, role, onChange }: MappingEditorProps) {
  const file = plan.files[role]
  if (!file) return null
  const schema = SCHEMAS[ROLE_SCHEMA[role]]
  const missing = schema.required.filter((field) => !file.mapping[field])
  return (
    <div className="mapping">
      {missing.map((field) => (
        <label key={field}>
          <span>{field}</span>
          <select value="" onChange={(event) => onChange(withMapping(plan, role, field, event.target.value))}>
            <option value="">Elige columna</option>
            {file.headers.map((header) => (
              <option key={header} value={header}>
                {header}
              </option>
            ))}
          </select>
        </label>
      ))}
    </div>
  )
}
