import type { SourceFile } from './preflight'
import { saveFolderHandle } from './log'

type WalkableDirectory = FileSystemDirectoryHandle & {
  values(): AsyncIterable<FileSystemFileHandle | FileSystemDirectoryHandle>
}

type DirectoryPicker = (options?: { id?: string; mode?: 'read' }) => Promise<FileSystemDirectoryHandle>

async function walk(dir: FileSystemDirectoryHandle, prefix = ''): Promise<SourceFile[]> {
  const found: SourceFile[] = []
  for await (const entry of (dir as WalkableDirectory).values()) {
    const path = `${prefix}${entry.name}`
    if (entry.kind === 'directory') {
      found.push(...(await walk(entry as FileSystemDirectoryHandle, `${path}/`)))
    } else if (/\.(csv|pdf)$/i.test(entry.name)) {
      found.push({ path, file: await (entry as FileSystemFileHandle).getFile() })
    }
  }
  return found
}

export const canPickDirectory = (): boolean => 'showDirectoryPicker' in window

export async function pickDirectory(): Promise<{ name: string; sources: SourceFile[] } | null> {
  const picker = (window as unknown as { showDirectoryPicker: DirectoryPicker }).showDirectoryPicker
  try {
    const handle = await picker({ id: 'cuadra-data', mode: 'read' })
    await saveFolderHandle(handle)
    return { name: handle.name, sources: await walk(handle) }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return null
    throw error
  }
}

type PermissionHandle = FileSystemDirectoryHandle & {
  requestPermission(options: { mode: 'read' }): Promise<PermissionState>
}

export async function reopenDirectory(
  handle: FileSystemDirectoryHandle,
): Promise<{ name: string; sources: SourceFile[] } | null> {
  const state = await (handle as PermissionHandle).requestPermission({ mode: 'read' })
  if (state !== 'granted') return null
  return { name: handle.name, sources: await walk(handle) }
}

export function sourcesFromInput(files: FileList): { name: string; sources: SourceFile[] } {
  const list = [...files]
  const first = list[0] as (File & { webkitRelativePath?: string }) | undefined
  const name = first?.webkitRelativePath?.split('/')[0] ?? 'datos'
  return {
    name,
    sources: list
      .filter((f) => /\.(csv|pdf)$/i.test(f.name))
      .map((file) => ({
        path: (file as File & { webkitRelativePath?: string }).webkitRelativePath?.split('/').slice(1).join('/') || file.name,
        file,
      })),
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
export async function fetchSample(kind: string): Promise<{ name: string; sources: SourceFile[] }> {
  const base = `${import.meta.env.BASE_URL}sample/${kind}/`
  const names = kind === 'train' || kind === 'big' ? [...SAMPLE_FILES, 'golden.csv'] : SAMPLE_FILES
  const pdfs = Array.from({ length: 12 }, (_, i) => `pdfs/remesa_${String(i + 1).padStart(3, '0')}.pdf`)
  const sources = await Promise.all(
    [...names, ...pdfs].map(async (path) => {
      const response = await fetch(`${base}${path}`)
      if (!response.ok) return null
      const type = response.headers.get('content-type') ?? ''
      if (type.includes('text/html')) return null
      const blob = await response.blob()
      return { path, file: new File([blob], path.split('/').at(-1) ?? path, { lastModified: 0 }) }
    }),
  )
  return { name: `ejemplo-${kind}`, sources: sources.filter((s): s is SourceFile => s !== null) }
}
