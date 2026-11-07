import { Check, Circle, FolderOpen } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Robot } from '../components/Robot'
import { engineFileProblem, PHASE_LABEL, type PhaseLayout, type PhaseName } from '../data/dataRoot'
import { readFolderHandle } from '../data/log'
import { buildPlan, fileSource, planProblems, withMapping, type Plan, type SourceFile } from '../data/preflight'
import { ROLE_LABEL, ROLE_SCHEMA, SCHEMAS, type FileRole } from '../data/schema'
import { canPickDirectory, fetchSample, pickDirectory, reopenDirectory, scanFromInput, type FolderScan } from '../data/sources'
import { formatCount } from '../domain/money'
import { useStore } from '../store'

const NEEDED: FileRole[] = ['bank', 'ar', 'ap', 'cn', 'open']
const USEFUL: FileRole[] = ['proposals', 'golden', 'categories', 'customers', 'suppliers', 'fx', 'terms', 'pdfText']
const DEFAULT_PHASES: PhaseName[] = ['live', 'final', 'sample', 'train']

const defaultLayout = (layouts: PhaseLayout[]) =>
  DEFAULT_PHASES.flatMap((phase) => layouts.filter((layout) => layout.phase === phase))[0] ?? null

export function LoadScreen() {
  const [scan, setScan] = useState<FolderScan | null>(null)
  const [layout, setLayout] = useState<PhaseLayout | null>(null)
  const [sources, setSources] = useState<SourceFile[]>([])
  const [plan, setPlan] = useState<Plan | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reading, setReading] = useState(false)
  const [lastFolder, setLastFolder] = useState<FileSystemDirectoryHandle | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const progress = useStore((s) => s.progress)
  const loadError = useStore((s) => s.loadError)
  const loadDataset = useStore((s) => s.loadDataset)

  useEffect(() => {
    readFolderHandle().then(setLastFolder, () => setLastFolder(null))
  }, [])

  const openPhase = useCallback(async (folder: FolderScan, next: PhaseLayout | null, phaseHint: string | null = null) => {
    setReading(true)
    setError(null)
    try {
      setLayout(next)
      const found = await folder.load(next)
      const phase = next?.phase ?? phaseHint
      setSources(found)
      setPlan(await buildPlan(phase ? `${folder.name}/${phase}` : folder.name, found, { phase }))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setReading(false)
    }
  }, [])

  const accept = useCallback(
    async (folder: FolderScan, phaseHint: string | null = null) => {
      setScan(folder)
      await openPhase(folder, defaultLayout(folder.layouts), phaseHint)
    },
    [openPhase],
  )

  const run = useCallback(
    async (open: () => Promise<FolderScan | null>, phaseHint: string | null = null) => {
      try {
        const picked = await open()
        if (picked) await accept(picked, phaseHint)
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

  const changeProposal = async (path: string) => {
    if (!plan) return
    setError(null)
    setPlan(await buildPlan(plan.name, sources, { phase: plan.phase, proposalPath: path }))
  }

  /** The engine may have written elsewhere: its folder cannot be walked when the data is behind symlinks. */
  const pickEngineFile = async (file: File) => {
    if (!plan) return
    const problem = engineFileProblem(file.name, plan.phase)
    if (problem) {
      setError(problem)
      return
    }
    const path = `out/${file.name}`
    const next = [...sources.filter((source) => source.path !== path), fileSource(path, file)]
    try {
      const built = await buildPlan(plan.name, next, { phase: plan.phase, proposalPath: path })
      if (built.files.proposals?.path !== path) {
        setError(`${file.name} no es un fichero de propuestas. Elige el explain_<fase>.csv.gz que escribe el motor.`)
        return
      }
      setSources(next)
      setPlan(built)
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  const problems = plan ? planProblems(plan) : []
  const ready = Boolean(plan) && problems.length === 0 && !progress && !reading
  const missingMasters = plan && !plan.files.categories && !plan.files.fx

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const sample = params.get('demo')
    if (!sample) return
    void (async () => {
      const folder = await fetchSample(sample)
      const found = await folder.load(null)
      const built = await buildPlan(folder.name, found, { phase: sample })
      setSources(found)
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
      <section className="load-main">
        <div className="load-brand"><Robot mood={plan ? (problems.length ? 'worried' : 'happy') : 'neutral'} size={40} /><span>Cuadra</span></div>
        <h1 className="load-title">Cuadra cada movimiento del banco con sus partidas.</h1>
        <p className="load-lede">
          Revisa las propuestas del pipeline por teclado, corrige lo dudoso y comprueba el formato oficial. Todo se queda en este navegador.
        </p>

        <div className="load-pick">
          <button className={plan ? 'btn btn-large' : 'btn btn-primary btn-large'} onClick={choose}>
            <span className="load-pick-label"><FolderOpen size={16} aria-hidden="true" /> {plan ? plan.name : 'Elegir la carpeta de datos'}</span>
            {plan && <span className="muted">Cambiar</span>}
          </button>
          <input
            ref={inputRef}
            type="file"
            hidden
            multiple
            {...({ webkitdirectory: '' } as object)}
            onChange={(event) => {
              if (event.target.files?.length) void accept(scanFromInput(event.target.files))
            }}
          />
          {!plan && <p className="footnote">La carpeta que contiene <code>live/</code>, <code>train/</code> y <code>masters/</code>, o la raíz del repositorio con <code>data/</code>. Usa Chrome o Edge para poder reabrirla.</p>}
        </div>

        {lastFolder && !plan && (
          <button className="btn" onClick={() => void run(() => reopenDirectory(lastFolder))}>Reabrir {lastFolder.name}</button>
        )}

        {scan && scan.layouts.length > 0 && (
          <div className="load-field">
            <span className="section-title">Fase</span>
            <div className="phase-picker" role="group" aria-label="Fase">
              {scan.layouts.map((option) => (
                <button
                  key={option.phase}
                  className={layout?.phase === option.phase ? 'btn is-active' : 'btn'}
                  aria-pressed={layout?.phase === option.phase}
                  onClick={() => void openPhase(scan, option)}
                >
                  {PHASE_LABEL[option.phase]}
                </button>
              ))}
            </div>
          </div>
        )}

        {plan && plan.proposalOptions.length > 1 && (
          <label className="load-field">
            <span className="section-title">Propuestas</span>
            <select value={plan.files.proposals?.path ?? ''} onChange={(event) => void changeProposal(event.target.value)}>
              {plan.proposalOptions.map((path) => <option key={path} value={path}>{path}</option>)}
            </select>
            {plan.enrichments.length > 0 && <span className="footnote">La confianza y el origen salen de {plan.enrichments.length} etapas de matching.</span>}
          </label>
        )}

        {plan && <EngineFilePicker onPick={(file) => void pickEngineFile(file)} />}

        {(error || loadError) && <p className="problem" role="alert">No se pudieron leer los datos: {error ?? loadError}</p>}
        {missingMasters && (
          <p className="problem">No encuentro los maestros: sin ellos no hay categorías ni tipos de cambio. Elige la carpeta raíz, la que contiene esta fase y <code>masters/</code>.</p>
        )}
        {problems.map((problem) => <p key={problem.message} className="problem">{problem.message}</p>)}

        {plan && (progress ? (
          <div className="progress" role="status">
            <Robot mood="working" size={28} />
            <div className="progress-text">
              <span>{progress.step}</span>
              <div className="meter" aria-hidden="true"><div style={{ transform: `scaleX(${progress.done / progress.total})` }} /></div>
            </div>
          </div>
        ) : (
          <button className="btn btn-primary btn-large load-go" disabled={!ready} onClick={() => plan && void loadDataset(plan)}>
            {reading ? 'Leyendo la carpeta…' : 'Cargar datos'}
            <kbd>Enter</kbd>
          </button>
        ))}

        {!plan && (
          <div className="load-samples">
            <span className="footnote">¿Sin datos a mano? Prueba con datos inventados:</span>
            <button className="link footnote" onClick={() => void run(() => fetchSample('train'), 'train')}>con golden</button>
            <button className="link footnote" onClick={() => void run(() => fetchSample('live'), 'live')}>sin golden</button>
          </div>
        )}
      </section>

      {plan && (
        <section className="load-files" aria-label="Ficheros detectados">
          <h2 className="section-title">Lo que se ha encontrado</h2>
          <FileList plan={plan} roles={NEEDED} onChange={setPlan} />
          <h2 className="section-title load-files-second">Lo que ayuda a decidir</h2>
          <FileList plan={plan} roles={USEFUL} onChange={setPlan} />
          <div className="file-row is-found">
            <Check size={14} aria-hidden="true" />
            <span>Documentos PDF</span>
            <span className="muted num">{formatCount(plan.pdfs.length)}</span>
          </div>
        </section>
      )}
    </div>
  )
}

function EngineFilePicker({ onPick }: { onPick: (file: File) => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  return (
    <p className="footnote">
      ¿El motor escribió en otra carpeta?{' '}
      <button className="link footnote" onClick={() => inputRef.current?.click()}>Elegir su fichero explain</button>
      <input
        ref={inputRef}
        type="file"
        hidden
        accept=".csv,.gz"
        aria-label="Fichero del motor"
        onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) onPick(file)
        }}
      />
    </p>
  )
}

function FileList({ plan, roles, onChange }: { plan: Plan; roles: FileRole[]; onChange: (plan: Plan) => void }) {
  return (
    <ul className="file-list">
      {roles.map((role) => {
        const file = plan.files[role]
        return (
          <li key={role} className={file ? 'file-row is-found' : 'file-row'}>
            {file ? <Check size={14} aria-label="Encontrado" /> : <Circle size={14} aria-label="No está" />}
            <span>{ROLE_LABEL[role]}</span>
            <span className="muted num">{file ? formatSize(file.file.size) : 'No está'}</span>
            {file && !file.complete && <MappingEditor plan={plan} role={role} onChange={onChange} />}
          </li>
        )
      })}
    </ul>
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
            {file.headers.map((header) => <option key={header} value={header}>{header}</option>)}
          </select>
        </label>
      ))}
    </div>
  )
}
