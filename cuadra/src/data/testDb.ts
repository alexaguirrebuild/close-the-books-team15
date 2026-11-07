import { createRequire } from 'node:module'
import path from 'node:path'
import { normalizeRow, type Db, type Row } from './db'

const require = createRequire(import.meta.url)
const duckdb = require('@duckdb/duckdb-wasm/dist/duckdb-node-blocking.cjs')

/** DuckDB for the tests, with the same engine and interface the browser uses. */
export function createNodeDb(): Promise<Db> {
  const dist = path.dirname(require.resolve('@duckdb/duckdb-wasm/dist/duckdb-node-blocking.cjs'))
  const bundles = {
    mvp: { mainModule: path.join(dist, 'duckdb-mvp.wasm'), mainWorker: path.join(dist, 'duckdb-node-mvp.worker.cjs') },
    eh: { mainModule: path.join(dist, 'duckdb-eh.wasm'), mainWorker: path.join(dist, 'duckdb-node-eh.worker.cjs') },
  }
  return duckdb
    .createDuckDB(bundles, new duckdb.VoidLogger(), duckdb.NODE_RUNTIME)
    .then(async (db: Record<string, (...args: unknown[]) => unknown>) => {
      await db.instantiate()
      const conn = db.connect() as { query: (sql: string) => { toArray: () => { toJSON: () => Row }[] } }
      return {
        query: async (sql: string) => conn.query(sql).toArray().map((r) => normalizeRow(r.toJSON())),
        registerFile: async (name: string, file: File) =>
          void db.registerFileBuffer(name, new Uint8Array(await file.arrayBuffer())),
        registerText: async (name: string, content: string) => void db.registerFileText(name, content),
        readFile: async (name: string) => db.copyFileToBuffer(name) as Uint8Array,
        dropFile: async (name: string) => void db.dropFile(name),
      } satisfies Db
    })
}
