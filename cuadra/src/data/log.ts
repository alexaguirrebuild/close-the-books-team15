import type { LogEntry } from './decisions'

const DB_NAME = 'cuadra'
const STORE = 'log'
const HANDLE_STORE = 'handles'
const VERSION = 1

type StoredEntry = { dataset: string; entry: LogEntry }

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, VERSION)
    request.onupgradeneeded = () => {
      const db = request.result
      const store = db.createObjectStore(STORE, { autoIncrement: true })
      store.createIndex('dataset', 'dataset')
      db.createObjectStore(HANDLE_STORE)
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('No se pudo abrir el almacenamiento local'))
  })
}

function promisify<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('Fallo en el almacenamiento local'))
  })
}

export async function appendLog(dataset: string, entry: LogEntry): Promise<void> {
  const db = await open()
  try {
    const stored: StoredEntry = { dataset, entry }
    await promisify(db.transaction(STORE, 'readwrite').objectStore(STORE).add(stored))
  } finally {
    db.close()
  }
}

export async function readLog(dataset: string): Promise<LogEntry[]> {
  const db = await open()
  try {
    const index = db.transaction(STORE).objectStore(STORE).index('dataset')
    const rows = (await promisify(index.getAll(dataset))) as StoredEntry[]
    return rows.map((r) => r.entry)
  } finally {
    db.close()
  }
}

export async function saveFolderHandle(handle: FileSystemDirectoryHandle): Promise<void> {
  const db = await open()
  try {
    await promisify(db.transaction(HANDLE_STORE, 'readwrite').objectStore(HANDLE_STORE).put(handle, 'last'))
  } finally {
    db.close()
  }
}

export async function readFolderHandle(): Promise<FileSystemDirectoryHandle | null> {
  const db = await open()
  try {
    const handle = await promisify(db.transaction(HANDLE_STORE).objectStore(HANDLE_STORE).get('last'))
    return (handle as FileSystemDirectoryHandle | undefined) ?? null
  } finally {
    db.close()
  }
}
