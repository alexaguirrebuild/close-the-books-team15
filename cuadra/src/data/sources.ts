import { detectLayouts, filesOfLayout, shouldEnter, type PhaseLayout } from './dataRoot'
import { saveFolderHandle } from './log'
import { fileSource, type SourceFile } from './preflight'

type WalkableDirectory = FileSystemDirectoryHandle & {
  values(): AsyncIterable<FileSystemFileHandle | FileSystemDirectoryHandle>
}

type DirectoryPicker = (options?: { id?: string; mode?: 'read' }) => Promise<FileSystemDirectoryHandle>

const DATA_FILE = /\.(csv|csv\.gz|pdf)$/i
/** Folders of the repository that can sit next to the data and hold nothing to load. */
const NEVER_ENTER = new Set(['node_modules', 'dist', 'cache', 'cuadra', 'pipeline', '__pycache__'])
const SCAN_DEPTH = 2

async function walk(
  dir: FileSystemDirectoryHandle,
  enter: (path: string) => boolean,
  prefix = '',
): Promise<SourceFile[]> {
  const found: SourceFile[] = []
  for await (const entry of (dir as WalkableDirectory).values()) {
    const path = `${prefix}${entry.name}`
    if (entry.kind === 'directory') {
      if (entry.name.startsWith('.') || NEVER_ENTER.has(entry.name) || !enter(path)) continue
      found.push(...(await walk(entry as FileSystemDirectoryHandle, enter, `${path}/`)))
    } else if (DATA_FILE.test(entry.name)) {
      const handle = entry as FileSystemFileHandle
      found.push({ path, open: () => handle.getFile() })
    }
  }
  return found
}

/** What a chosen folder offers: its phases, if it is a data root, and how to read one of them. */
export type FolderScan = {
  name: string
  layouts: PhaseLayout[]
  /** With null, the whole folder is a single dataset. */
  load: (layout: PhaseLayout | null) => Promise<SourceFile[]>
}

async function scanHandle(handle: FileSystemDirectoryHandle): Promise<FolderScan> {
  const shallow = await walk(handle, (path) => path.split('/').length <= SCAN_DEPTH)
  return {
    name: handle.name,
    layouts: detectLayouts(shallow.map((source) => source.path)),
    load: async (layout) => {
      const files = await walk(handle, (path) => (layout ? shouldEnter(layout, path) : true))
      return layout ? filesOfLayout(layout, files) : files
    },
  }
}

export const canPickDirectory = (): boolean => 'showDirectoryPicker' in window

export async function pickDirectory(): Promise<FolderScan | null> {
  const picker = (window as unknown as { showDirectoryPicker: DirectoryPicker }).showDirectoryPicker
  try {
    const handle = await picker({ id: 'cuadra-data', mode: 'read' })
    await saveFolderHandle(handle)
    return await scanHandle(handle)
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return null
    throw error
  }
}

type PermissionHandle = FileSystemDirectoryHandle & {
  requestPermission(options: { mode: 'read' }): Promise<PermissionState>
}

export async function reopenDirectory(handle: FileSystemDirectoryHandle): Promise<FolderScan | null> {
  const state = await (handle as PermissionHandle).requestPermission({ mode: 'read' })
  return state === 'granted' ? scanHandle(handle) : null
}

export function scanFromInput(files: FileList): FolderScan {
  const list = [...files] as (File & { webkitRelativePath?: string })[]
  const sources = list
    .filter((file) => DATA_FILE.test(file.name))
    .map((file) => fileSource(file.webkitRelativePath?.split('/').slice(1).join('/') || file.name, file))
  return {
    name: list[0]?.webkitRelativePath?.split('/')[0] ?? 'datos',
    layouts: detectLayouts(sources.map((source) => source.path)),
    load: async (layout) => (layout ? filesOfLayout(layout, sources) : sources),
  }
}

const SAMPLE_FILES = [
  'bank_transactions.csv',
  'ar_invoices.csv',
  'ap_invoices.csv',
  'credit_notes.csv',
  'open_items_at_start.csv',
  'propuestas.csv',
  'pdfs_text.csv',
  'masters/categories.csv',
  'masters/customers.csv',
  'masters/suppliers.csv',
  'masters/fx_rates.csv',
  'masters/payment_terms.csv',
]

/** Loads a bundled synthetic dataset. These figures are made up and never apply to a real submission. */
export async function fetchSample(kind: string): Promise<FolderScan> {
  const base = `${import.meta.env.BASE_URL}sample/${kind}/`
  const names = kind === 'train' || kind === 'big' ? [...SAMPLE_FILES, 'golden_allocations.csv'] : SAMPLE_FILES
  const pdfs = Array.from({ length: 12 }, (_, i) => `pdfs/remesa_${String(i + 1).padStart(3, '0')}.pdf`)
  const fetched = await Promise.all(
    [...names, ...pdfs].map(async (path) => {
      const response = await fetch(`${base}${path}`)
      if (!response.ok) return null
      const type = response.headers.get('content-type') ?? ''
      if (type.includes('text/html')) return null
      const blob = await response.blob()
      return fileSource(path, new File([blob], path.split('/').at(-1) ?? path, { lastModified: 0 }))
    }),
  )
  const sources = fetched.filter((source): source is SourceFile => source !== null)
  return { name: `ejemplo-${kind}`, layouts: [], load: async () => sources }
}
