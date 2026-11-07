import * as duckdb from '@duckdb/duckdb-wasm'
import ehWasm from '@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url'
import mvpWasm from '@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url'
import ehWorker from '@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url'
import mvpWorker from '@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url'
import { normalizeRow, type Db, type PriorityOf, type Row } from './db'

const BUNDLES: duckdb.DuckDBBundles = {
  mvp: { mainModule: mvpWasm, mainWorker: mvpWorker },
  eh: { mainModule: ehWasm, mainWorker: ehWorker },
}

type Task = { run: () => Promise<void>; priority: PriorityOf }

const isHigh = (task: Task) =>
  (typeof task.priority === 'function' ? task.priority() : task.priority) === 'high'

/** DuckDB runs one query at a time: the keyboard path always goes before prefetches. */
function createScheduler() {
  const tasks: Task[] = []
  let running = false

  const pump = async () => {
    if (running) return
    running = true
    while (tasks.length) {
      const urgent = tasks.findIndex(isHigh)
      const [task] = tasks.splice(Math.max(urgent, 0), 1)
      await task.run()
    }
    running = false
  }

  return <T>(work: () => Promise<T>, priority: PriorityOf): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      tasks.push({ run: () => work().then(resolve, reject), priority })
      void pump()
    })
}

export async function createBrowserDb(): Promise<Db> {
  const bundle = await duckdb.selectBundle(BUNDLES)
  const worker = new Worker(bundle.mainWorker as string)
  const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker)
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker)
  const conn = await db.connect()
  const schedule = createScheduler()

  return {
    query: (sql, priority = 'high') =>
      schedule(async () => {
        const table = await conn.query(sql)
        return table.toArray().map((row) => normalizeRow(row.toJSON() as Row))
      }, priority),
    registerFile: (name, file) =>
      db.registerFileHandle(name, file, duckdb.DuckDBDataProtocol.BROWSER_FILEREADER, true),
    registerText: (name, text) => db.registerFileText(name, text),
    readFile: (name) => schedule(() => db.copyFileToBuffer(name), 'high'),
    dropFile: (name) => db.dropFile(name).then(() => undefined),
  }
}
