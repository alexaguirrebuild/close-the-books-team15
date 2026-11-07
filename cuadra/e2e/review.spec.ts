import { execFileSync } from 'node:child_process'
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { gunzipSync, gzipSync } from 'node:zlib'
import { expect, test, type Download, type Page } from '@playwright/test'

const currentTxn = (page: Page) => page.locator('.txn-id')
const submissionText = async (download: Download) => gunzipSync(readFileSync(await download.path())).toString('utf8')
const allocInput = (page: Page) => page.locator('.alloc-row input').first()
const viewTab = (page: Page, name: RegExp) => page.getByRole('tab', { name })

async function openSample(page: Page, sample: 'train' | 'live') {
  await page.goto(`/?demo=${sample}&autoload=1`)
  await expect(page.locator('.rail')).toBeVisible({ timeout: 30_000 })
}

async function openReview(page: Page) {
  await page.keyboard.press('Alt+Digit2')
  await expect(currentTxn(page)).toBeVisible()
}

async function goToCaseThatAddsUp(page: Page) {
  await viewTab(page, /^Listas/).click()
  await expect(page.locator('.bulk-row')).toBeVisible()
  await expect(currentTxn(page)).toBeVisible()
  for (let i = 0; i < 200; i += 1) {
    const hasRows = await page.locator('.alloc-row').first().isVisible()
    const title = await page.locator('.balance-title').textContent()
    if (hasRows && title === 'Cuadra exacto') return
    const before = await currentTxn(page).textContent()
    await page.keyboard.press('j')
    await expect(currentTxn(page)).not.toHaveText(before ?? '')
  }
  throw new Error('No se encontró un movimiento con propuesta que cuadre')
}

test.describe('deciding a movement', () => {
  test('confirms a proposal with Enter, moves on and offers to undo', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    await goToCaseThatAddsUp(page)
    const before = await currentTxn(page).textContent()
    await page.keyboard.press('Enter')
    await expect(currentTxn(page)).not.toHaveText(before ?? '')
    await expect(page.locator('.pace')).toContainText('1 en esta sesión')
    await expect(page.locator('.notice')).toContainText(`Confirmado ${before}`)
    await expect(page.getByRole('button', { name: /Deshacer/ })).toBeVisible()
    await expect(page.locator('.queue-row.is-done').first()).toBeVisible()
  })

  test('asks for the exception, then saves the typed amount', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    await goToCaseThatAddsUp(page)
    await page.keyboard.press('Tab')
    await expect(allocInput(page)).toBeFocused()
    await page.keyboard.type('12,5')
    await expect(page.locator('.balance-title')).toContainText('por aplicar')

    const before = await currentTxn(page).textContent()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('dialog', { name: 'Excepción del movimiento' })).toBeVisible()
    await page.keyboard.press('1')
    await expect(page.locator('.line-exception')).toContainText('Pago parcial')
    await expect(allocInput(page)).toHaveValue('12,5')
    await page.keyboard.press('Enter')
    await expect(currentTxn(page)).not.toHaveText(before ?? '')

    await page.keyboard.press('Alt+Digit3')
    const button = page.getByRole('button', { name: /Descargar submission/ })
    await expect(button).toBeEnabled({ timeout: 20_000 })
    const [download] = await Promise.all([page.waitForEvent('download'), button.click()])
    expect(await submissionText(download)).toMatch(new RegExp(`^${before},[^,]+,12\\.50,,PARTIAL$`, 'm'))
  })

  test('an accepted short payment clears the whole balance by itself', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    await goToCaseThatAddsUp(page)
    const balance = (await page.locator('.alloc-open').first().textContent()) ?? ''
    await page.keyboard.press('Tab')
    await page.keyboard.type('1')
    await page.keyboard.press('Escape')
    await page.keyboard.press('e')
    await page.keyboard.press('3')
    await expect(page.locator('.line-exception')).toContainText('Corto por descuento')
    const expected = balance.replace('Saldo ', '').split(' ')[0]
    await expect(allocInput(page)).toHaveValue(expected)
    await expect(page.locator('.balance')).toHaveAttribute('data-status', 'explained')
  })

  test('Shift+Enter saves without the exception when the reviewer insists', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    await goToCaseThatAddsUp(page)
    await page.keyboard.press('Tab')
    await page.keyboard.type('12,5')
    const before = await currentTxn(page).textContent()
    await page.keyboard.press('Shift+Enter')
    await expect(currentTxn(page)).not.toHaveText(before ?? '')
  })

  test('keeps the work on a movement when moving away from it', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    await goToCaseThatAddsUp(page)
    const here = await currentTxn(page).textContent()
    await page.keyboard.press('Tab')
    await page.keyboard.type('12,5')
    await page.keyboard.press('Escape')
    await expect(page.locator('.case-flags')).toContainText('Borrador')
    await page.keyboard.press('j')
    await expect(currentTxn(page)).not.toHaveText(here ?? '')
    await page.keyboard.press('k')
    await expect(currentTxn(page)).toHaveText(here ?? '')
    await expect(allocInput(page)).toHaveValue('12,50')
    await expect(page.locator('.case-flags')).toContainText('Borrador')
  })

  test('undo brings back the movement with its proposal', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    await goToCaseThatAddsUp(page)
    const before = await currentTxn(page).textContent()
    const value = await allocInput(page).inputValue()
    await page.keyboard.press('Enter')
    await expect(currentTxn(page)).not.toHaveText(before ?? '')
    await page.keyboard.press('z')
    await expect(currentTxn(page)).toHaveText(before ?? '')
    await expect(allocInput(page)).toHaveValue(value)
  })

  test('assigns a category only without items and marks unidentified', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    const first = await currentTxn(page).textContent()
    await page.keyboard.press('g')
    await page.keyboard.type('bank_fees')
    await page.keyboard.press('Enter')
    await expect(page.locator('.category-chip')).toContainText('Comisiones')
    await expect(page.locator('.alloc-row')).toHaveCount(0)
    await page.keyboard.press('Enter')
    await expect(currentTxn(page)).not.toHaveText(first ?? '')

    const second = await currentTxn(page).textContent()
    await page.keyboard.press('u')
    await expect(currentTxn(page)).not.toHaveText(second ?? '')
  })

  test('finds an item with the search box and adds it', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    await viewTab(page, /^Sin propuesta/).click()
    await expect(currentTxn(page)).toBeVisible()
    for (let i = 0; i < 30 && !(await page.locator('.cand-printed').first().isVisible()); i += 1) await page.keyboard.press('j')
    const printed = (await page.locator('.cand-printed').first().textContent()) ?? ''
    await page.keyboard.press('/')
    await page.keyboard.type(printed)
    await expect(page.locator('.search-row').first()).toContainText(printed)
    await page.keyboard.press('Enter')
    await expect(page.locator('.alloc-printed').first()).toContainText(printed)
  })

  test('opens a document inside the case and closes it without leaving', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    await viewTab(page, /^Todas/).click()
    await expect(currentTxn(page)).toBeVisible()
    await page.keyboard.press('b')
    await page.keyboard.type('BT-2024-0000002')
    await page.keyboard.press('Enter')
    await expect(currentTxn(page)).toHaveText('BT-2024-0000002')
    await expect(page.locator('.doc').first()).toBeVisible({ timeout: 10_000 })
    await page.keyboard.press('o')
    await expect(page.locator('.pdf-pane iframe')).toBeVisible()
    await expect(page.locator('.candidates')).toHaveCount(0)
    await page.keyboard.press('o')
    await expect(page.locator('.pdf-pane')).toHaveCount(0)
    await expect(page.locator('.candidates')).toBeVisible()
  })

  test('recovers the review after reloading the page', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    const decided = await currentTxn(page).textContent()
    await page.keyboard.press('u')
    await expect(currentTxn(page)).not.toHaveText(decided ?? '')

    await page.waitForTimeout(300)
    await openSample(page, 'train')
    await expect(page.locator('.notice')).toContainText('Revisión recuperada: 1')
    await expect(page.locator('.flow-node', { hasText: 'Hechas' }).locator('.flow-value')).toHaveText('1')
  })
})

test.describe('never deciding by accident', () => {
  test('holding Enter confirms one movement and no more', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    await goToCaseThatAddsUp(page)
    const before = await currentTxn(page).textContent()
    await page.keyboard.down('Enter')
    await expect(currentTxn(page)).not.toHaveText(before ?? '')
    for (let i = 0; i < 5; i += 1) await page.keyboard.down('Enter')
    await page.keyboard.up('Enter')
    await expect(page.locator('.pace')).toHaveText('1 en esta sesión')
  })

  test('Enter held on the summary opens the review without confirming anything', async ({ page }) => {
    await openSample(page, 'train')
    await expect(page.locator('.summary-title')).toBeVisible()
    await page.keyboard.down('Enter')
    await expect(currentTxn(page)).toBeVisible()
    for (let i = 0; i < 5; i += 1) await page.keyboard.down('Enter')
    await page.keyboard.up('Enter')
    await expect(page.locator('.pace')).toHaveText('0 en esta sesión')
  })

  test('two quick undos restore both movements', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    const done = viewTab(page, /^Hechas/).locator('.view-count')
    for (let i = 0; i < 2; i += 1) {
      const before = await currentTxn(page).textContent()
      await page.keyboard.press('u')
      await expect(currentTxn(page)).not.toHaveText(before ?? '')
    }
    await expect(done).toHaveText('2')
    await page.keyboard.press('z')
    await page.keyboard.press('z')
    await expect(done).toHaveText('0', { timeout: 10_000 })
    await expect(page.locator('.queue-row.is-done')).toHaveCount(0)
  })

  test('adding another item keeps an amount already typed', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    await goToCaseThatAddsUp(page)
    await page.keyboard.press('Tab')
    await page.keyboard.type('12,5')
    await page.keyboard.press('Escape')
    await page.keyboard.press('m')
    const next = page.locator('.candidate:not(.is-selected) button').first()
    await expect(next).toBeVisible()
    await next.click()
    await expect(page.locator('.alloc-row')).toHaveCount(2)
    await expect(allocInput(page)).toHaveValue(/^12,50?$/)
  })

  test('while the filters are open, the keyboard does not touch the movement behind', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    await goToCaseThatAddsUp(page)
    const before = await currentTxn(page).textContent()
    await page.getByRole('button', { name: /Filtros/ }).click()
    await expect(page.locator('.filter-panel')).toBeVisible()
    await page.keyboard.press('Enter')
    await page.keyboard.press('j')
    await expect(page.locator('.filter-panel')).toBeVisible()
    await expect(currentTxn(page)).toHaveText(before ?? '')
    await expect(page.locator('.pace')).toHaveText('0 en esta sesión')
    await page.keyboard.press('Escape')
    await expect(page.locator('.filter-panel')).toHaveCount(0)
  })
})

test.describe('the list', () => {
  test('moves with the case, on one screen, by keyboard', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    const rows = page.locator('.queue-row')
    await expect(rows.first()).toHaveClass(/is-cursor/)
    const first = await currentTxn(page).textContent()
    await page.keyboard.press('j')
    await expect(rows.nth(1)).toHaveClass(/is-cursor/)
    await expect(currentTxn(page)).not.toHaveText(first ?? '')
    await page.keyboard.press('k')
    await expect(rows.first()).toHaveClass(/is-cursor/)
  })

  test('moving far down the list never drags the screen', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    for (let i = 0; i < 25; i += 1) await page.keyboard.press('j')
    await expect(page.locator('.queue-row.is-cursor')).toBeInViewport()
    await expect(page.locator('.scope')).toBeInViewport()
    await expect(page.locator('.case-head')).toBeInViewport()
    await expect(page.locator('.case-save')).toBeInViewport()
    const shifted = await page.evaluate(() => [document.documentElement, document.body, document.getElementById('root'), document.querySelector('.shell'), document.querySelector('.main'), document.querySelector('.review')].map((el) => el?.scrollTop ?? 0))
    expect(shifted).toEqual([0, 0, 0, 0, 0, 0])
  })

  test('opens a movement by clicking its row, and keeps the list', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    await page.locator('.queue-row').nth(3).click()
    await expect(page.locator('.queue-row').nth(3)).toHaveClass(/is-cursor/)
    await expect(page.locator('.case-head')).toBeVisible()
  })

  test('a filter does not keep the keyboard, and can be removed from its chip', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    const range = page.locator('.queue-range')
    const total = await range.textContent()
    await page.getByRole('button', { name: /Filtros/ }).click()
    await page.locator('.filter-panel label', { hasText: /^Divisa/ }).locator('select').selectOption('USD')
    await expect(range).not.toHaveText(total ?? '')
    await page.keyboard.press('Escape')
    await expect(page.locator('.filter-chip')).toContainText('USD')
    const rows = page.locator('.queue-row')
    await expect(rows.first()).toHaveClass(/is-cursor/)
    await page.keyboard.press('j')
    await expect(rows.nth(1)).toHaveClass(/is-cursor/)
    await page.getByRole('button', { name: 'Quitar filtro USD' }).click()
    await expect(range).toHaveText(total ?? '')
  })

  test('searches movements with B and gives the keyboard back', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    const id = (await currentTxn(page).textContent()) ?? ''
    await page.keyboard.press('b')
    await expect(page.getByLabel('Buscar movimiento')).toBeFocused()
    await page.keyboard.type(id)
    await page.keyboard.press('Enter')
    await expect(page.locator('.queue-range')).toContainText('de 1')
    await expect(currentTxn(page)).toHaveText(id)
  })

  test('cycles the views with V and hides the list with L', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    await expect(viewTab(page, /^Prioridad/)).toHaveAttribute('aria-selected', 'true')
    await page.keyboard.press('v')
    await expect(viewTab(page, /^Listas/)).toHaveAttribute('aria-selected', 'true')
    await page.keyboard.press('l')
    await expect(page.locator('.queue')).toHaveCount(0)
    await expect(page.locator('.case-head')).toBeVisible()
    await page.keyboard.press('l')
    await expect(page.locator('.queue')).toBeVisible()
  })

  test('confirms a batch after showing what it holds, and can undo it', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    const ready = viewTab(page, /^Listas/)
    await ready.click()
    const count = (await ready.locator('.view-count').textContent()) ?? ''
    await page.locator('.bulk-row .btn-primary').click()
    await expect(page.getByRole('dialog', { name: 'Confirmar en lote' })).toBeVisible()
    await expect(page.locator('.bulk-lines')).toHaveText(count)
    await page.keyboard.press('Enter')
    await expect(page.locator('.notice')).toContainText(`Confirmados ${count}`)
    await expect(ready.locator('.view-count')).toHaveText('0')
    await page.keyboard.press('z')
    await expect(ready.locator('.view-count')).toHaveText(count, { timeout: 10_000 })
  })
})

test.describe('sending', () => {
  test('includes a decision made right before opening it', async ({ page }) => {
    await openSample(page, 'train')
    await openReview(page)
    const decided = await currentTxn(page).textContent()
    await page.keyboard.press('u')
    await page.keyboard.press('Alt+Digit3')
    await expect(page.locator('.from-review')).toHaveText('1', { timeout: 20_000 })
    const button = page.getByRole('button', { name: /Descargar submission/ })
    await expect(button).toBeEnabled()
    const [download] = await Promise.all([page.waitForEvent('download'), button.click()])
    expect(await submissionText(download)).toContain(`${decided},,,UNIDENTIFIED,UNIDENTIFIED`)
  })

  test('reports what the server would reject with its row and blocks the download', async ({ page }) => {
    await openSample(page, 'train')
    await page.keyboard.press('Alt+Digit3')
    await page.evaluate(() => {
      const store = (window as unknown as { __cuadra: { getState(): { decide(rid: number, d: object): void } } }).__cuadra
      store.getState().decide(12, {
        txnId: 'BT-2024-0000012',
        allocations: [{ itemId: 'FACTURA-1', amount: 1, exception: null }],
        category: null,
        exception: null,
        source: 'manual',
        at: 1,
      })
    })
    await expect(page.locator('.issue-error')).toContainText('FACTURA-1', { timeout: 20_000 })
    await expect(page.getByRole('button', { name: 'Corrige 1 error' })).toBeDisabled()
  })

  test('does not block a file the server accepts, even with an item of another phase', async ({ page }) => {
    await openSample(page, 'train')
    await page.keyboard.press('Alt+Digit3')
    await page.evaluate(() => {
      const store = (window as unknown as { __cuadra: { getState(): { decide(rid: number, d: object): void } } }).__cuadra
      store.getState().decide(12, {
        txnId: 'BT-2024-0000012',
        allocations: [{ itemId: 'ARI-2021-9999999', amount: 1, exception: 'RETURNED' }],
        category: null,
        exception: null,
        source: 'manual',
        at: 1,
      })
    })
    await expect(page.locator('.issue-warning', { hasText: 'no está en esta fase' })).toBeVisible({ timeout: 20_000 })
    await expect(page.getByRole('button', { name: /Descargar submission/ })).toBeEnabled()
  })

  test('downloads a valid file that covers every movement, in the shapes FORMAT.md allows', async ({ page }) => {
    await openSample(page, 'train')
    await page.keyboard.press('Alt+Digit3')
    const button = page.getByRole('button', { name: /Descargar submission/ })
    await expect(button).toBeEnabled({ timeout: 20_000 })
    const [download] = await Promise.all([page.waitForEvent('download'), button.click()])
    await expect(page.locator('.send-main .problem')).toHaveCount(0)
    expect(download.suggestedFilename()).toBe('submission_train.csv.gz')
    const lines = (await submissionText(download)).trim().split('\n')
    expect(lines[0]).toBe('bank_txn_id,item_id,amount,category,exception')
    const txns = new Set(lines.slice(1).map((line) => line.split(',')[0]))
    expect(txns.size).toBe(3000)
    const pairs = lines.slice(1).map((line) => line.split(',').slice(0, 2).join('|'))
    expect(new Set(pairs).size).toBe(pairs.length)
    const itemRow = /^BT-\d{4}-\d{7},(ARI|ARC|API|APC)-\d{4}-\d{7},\d+\.\d{2},,[A-Z_]*$/
    const noItemRow = /^BT-\d{4}-\d{7},,,[A-Z_]+,[A-Z_]*$/
    expect(lines.slice(1).filter((line) => !itemRow.test(line) && !noItemRow.test(line))).toEqual([])
  })
})

test.describe('compressed data, as the real files are delivered', () => {
  test('recognises gzip by its bytes and loads it in the browser', async ({ page }) => {
    await page.route('**/sample/train/bank_transactions.csv', async (route) => {
      const response = await route.fetch()
      await route.fulfill({ body: gzipSync(await response.body()), contentType: 'application/gzip' })
    })
    await openSample(page, 'train')
    await expect(page.locator('.flow-node', { hasText: 'Movimientos' }).locator('.flow-value')).toHaveText('3.000')
  })
})

test.describe('no submission file where it could be uploaded', () => {
  test('in LIVE it validates and passes on the review, but offers no submission', async ({ page }) => {
    await openSample(page, 'live')
    await page.keyboard.press('Alt+Digit3')
    await expect(page.locator('.send-summary')).toBeVisible({ timeout: 20_000 })
    await expect(page.locator('.review-only')).toContainText('motor del equipo')
    await expect(page.getByRole('button', { name: /Descargar submission|sin comprimir/ })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Descargar mi revisión' })).toBeVisible()
  })
})

test.describe('summary and diagnosis', () => {
  test('says what to do next and goes there with Enter', async ({ page }) => {
    await openSample(page, 'train')
    await expect(page.locator('.summary-title')).toContainText('por revisar')
    await expect(page.locator('.flow-node.is-next')).toContainText('Por revisar')
    await page.keyboard.press('Enter')
    await expect(currentTxn(page)).toBeVisible()
  })

  test('scores against the golden with the official figures', async ({ page }) => {
    await openSample(page, 'train')
    await expect(page.locator('.score-total')).toHaveText(/^\d+,\d{2}$/, { timeout: 20_000 })
    await expect(page.locator('.note')).toContainText('evaluador oficial')
  })

  test('opens the errors of a segment, and shows what the golden expected', async ({ page }) => {
    await openSample(page, 'train')
    const worst = page.locator('.segments tbody tr').first()
    await expect(worst).toBeVisible({ timeout: 20_000 })
    const segment = (await worst.locator('td').first().textContent()) ?? ''
    await worst.click()
    await expect(viewTab(page, /^Errores/)).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('.filter-chip')).toContainText(segment)
    await expect(currentTxn(page)).toBeVisible()
    await expect(page.locator('.trail-step', { hasText: 'Golden' })).toBeVisible()
  })

  test('shows no score and no errors without a golden', async ({ page }) => {
    await openSample(page, 'live')
    await expect(page.locator('.flow')).toBeVisible()
    await expect(page.locator('.score-total')).toHaveCount(0)
    await page.keyboard.press('Alt+Digit2')
    await expect(viewTab(page, /^Errores/)).toHaveCount(0)
  })
})

test.describe('engine output', () => {
  const SAMPLE = join('public', 'sample', 'train')
  const HEADER = 'bank_txn_id,item_id,amount,category,exception'
  let folder: string

  /** The official columns plus the rule and the evidence, as bankrec.run writes them. */
  const explainFile = () => {
    const [, ...rows] = readFileSync(join(SAMPLE, 'propuestas.csv'), 'utf8').trim().split('\n')
    const body = rows.map((row) => {
      const official = row.split(',').slice(0, 5)
      return [...official, 'dd_batch', `remesa ${official[0]}`].join(',')
    })
    return [`${HEADER},rule,evidence`, ...body].join('\n')
  }

  async function expectEngineReview(page: Page) {
    await page.getByRole('button', { name: /Cargar datos/ }).click()
    await expect(page.locator('.rail')).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('.flow')).not.toContainText('Listas')
    await openReview(page)
    await expect(viewTab(page, /^Listas/)).toHaveCount(0)
    await expect(page.locator('.view-hint')).toContainText('no da confianza')
    await expect(page.getByText(/Remesa de recibos: remesa/)).toBeVisible()
  }

  test.beforeEach(() => {
    folder = mkdtempSync(join(tmpdir(), 'cuadra-engine-'))
  })

  test.afterEach(() => {
    rmSync(folder, { recursive: true, force: true })
  })

  test('reads the repository as the engine leaves it, without being told where anything is', async ({ page }) => {
    const repo = join(folder, 'repo')
    mkdirSync(join(repo, 'out', 'train'), { recursive: true })
    cpSync(SAMPLE, join(repo, 'data', 'train'), { recursive: true, filter: (from) => !/propuestas\.csv|masters|pdfs/.test(from) })
    cpSync(join(SAMPLE, 'masters'), join(repo, 'data', 'masters'), { recursive: true })
    writeFileSync(join(repo, 'out', 'explain_train.csv'), explainFile())
    writeFileSync(join(repo, 'out', 'train', 'baseline.csv'), `${HEADER}\n`)

    await page.goto('/')
    await page.locator('input[type=file]').setInputFiles(repo)
    await expect(page.getByLabel('Propuestas')).toHaveValue('out/explain_train.csv')
    await expectEngineReview(page)
  })

  test('accepts a file of the engine from another folder, for when the data is behind symlinks', async ({ page }) => {
    const explain = join(folder, 'explain_train.csv')
    writeFileSync(explain, explainFile())

    await page.goto('/')
    await page.getByRole('button', { name: 'con golden' }).click()
    await expect(page.getByRole('button', { name: /Cargar datos/ })).toBeEnabled({ timeout: 30_000 })
    await page.getByLabel('Fichero del motor').setInputFiles(explain)
    await expect(page.getByLabel('Propuestas')).toHaveValue('out/explain_train.csv')
    await expectEngineReview(page)
  })

  test('says so when the chosen file holds no proposals, and keeps what was loaded', async ({ page }) => {
    const notes = join(folder, 'notas.txt')
    writeFileSync(notes, 'nada que ver')

    await page.goto('/')
    await page.getByRole('button', { name: 'con golden' }).click()
    await expect(page.getByRole('button', { name: /Cargar datos/ })).toBeEnabled({ timeout: 30_000 })
    await page.getByLabel('Fichero del motor').setInputFiles(notes)
    await expect(page.getByRole('alert')).toContainText('notas.txt no es un fichero de propuestas')
    await expect(page.getByRole('button', { name: /Cargar datos/ })).toBeEnabled()
  })

  test('refuses a file of the engine written for another phase, and forgets the complaint on the next choice', async ({ page }) => {
    const otherPhase = join(folder, 'explain_live.csv')
    const right = join(folder, 'explain_train.csv')
    writeFileSync(otherPhase, explainFile())
    writeFileSync(right, explainFile())

    await page.goto('/')
    await page.getByRole('button', { name: 'con golden' }).click()
    await expect(page.getByRole('button', { name: /Cargar datos/ })).toBeEnabled({ timeout: 30_000 })
    await page.getByLabel('Fichero del motor').setInputFiles(otherPhase)
    await expect(page.getByRole('alert')).toContainText('explain_live.csv es de otra fase (LIVE)')
    await expect(page.getByLabel('Propuestas')).toHaveCount(0)

    await page.getByLabel('Fichero del motor').setInputFiles(right)
    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(page.getByLabel('Propuestas')).toHaveValue('out/explain_train.csv')
  })

  test('does not offer to filter by a confidence the engine does not give', async ({ page }) => {
    const explain = join(folder, 'explain_train.csv')
    writeFileSync(explain, explainFile())

    await page.goto('/')
    await page.getByRole('button', { name: 'con golden' }).click()
    await expect(page.getByRole('button', { name: /Cargar datos/ })).toBeEnabled({ timeout: 30_000 })
    await page.getByLabel('Fichero del motor').setInputFiles(explain)
    await page.getByRole('button', { name: /Cargar datos/ }).click()
    await expect(page.locator('.rail')).toBeVisible({ timeout: 30_000 })
    await page.keyboard.press('Alt+Digit3')
    await expect(page.locator('.send-summary')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/confianza/i)).toHaveCount(0)
  })

  test('forgets the confidence filter when another dataset loads', async ({ page }) => {
    const minConfidence = () =>
      page.evaluate(() => (window as unknown as { __cuadra: { getState(): { exportOptions: { minConfidence: number } } } }).__cuadra.getState().exportOptions.minConfidence)

    await page.goto('/')
    await page.getByRole('button', { name: 'con golden' }).click()
    await page.getByRole('button', { name: /Cargar datos/ }).click()
    await expect(page.locator('.rail')).toBeVisible({ timeout: 30_000 })
    await page.keyboard.press('Alt+Digit3')
    await page.getByLabel(/Solo con confianza desde/).fill('80')
    await expect.poll(minConfidence).toBe(0.8)

    await page.getByRole('button', { name: 'Datos' }).click()
    await page.getByRole('button', { name: 'con golden' }).click()
    await page.getByRole('button', { name: /Cargar datos/ }).click()
    await expect(page.locator('.rail')).toBeVisible({ timeout: 30_000 })
    await expect.poll(minConfidence).toBe(0)
  })

  test('fills the width of the summary with the steps it shows', async ({ page }) => {
    const explain = join(folder, 'explain_train.csv')
    writeFileSync(explain, explainFile())

    await page.goto('/')
    await page.getByRole('button', { name: 'con golden' }).click()
    await expect(page.getByRole('button', { name: /Cargar datos/ })).toBeEnabled({ timeout: 30_000 })
    await page.getByLabel('Fichero del motor').setInputFiles(explain)
    await page.getByRole('button', { name: /Cargar datos/ }).click()
    await expect(page.locator('.flow-step')).toHaveCount(4, { timeout: 30_000 })
    const [flow, last] = await Promise.all([page.locator('.flow').boundingBox(), page.locator('.flow-step').last().boundingBox()])
    expect(Math.round((last?.x ?? 0) + (last?.width ?? 0))).toBe(Math.round((flow?.x ?? 0) + (flow?.width ?? 0)))
  })
})

/** The real data is not in the repository: BANKREC_DATA_ROOT=/path PW_CHANNEL=chrome npx playwright test -g "real data" */
const ROOT = process.env.BANKREC_DATA_ROOT
const PHASE = process.env.BANKREC_PHASE ?? 'sample'

test.describe('real data', () => {
  test.skip(!ROOT, 'Necesita BANKREC_DATA_ROOT')
  test.setTimeout(300_000)

  test('loads the folder as delivered, decides and exports in the browser', async ({ page }) => {
    // The browser does not follow symlinks in a folder upload, so this is a copy-on-write clone.
    const folder = mkdtempSync(join(tmpdir(), 'cuadra-root-'))
    const docs = join(ROOT as string, 'bankrec-docs-and-masters')
    const phaseDir = PHASE === 'sample' ? join(docs, 'sample') : join(ROOT as string, PHASE)
    const out = join(ROOT as string, 'out')
    mkdirSync(join(folder, 'root', 'out'), { recursive: true })
    execFileSync('cp', ['-cR', phaseDir, join(folder, 'root', PHASE)])
    execFileSync('cp', ['-cR', join(docs, 'masters'), join(folder, 'root', 'masters')])
    if (existsSync(join(out, PHASE))) execFileSync('cp', ['-cR', join(out, PHASE), join(folder, 'root', 'out', PHASE)])
    const runPrefixes = [`sub_${PHASE}.`, `explain_${PHASE}.`]
    for (const name of readdirSync(out).filter((file) => runPrefixes.some((prefix) => file.startsWith(prefix)))) {
      execFileSync('cp', ['-c', join(out, name), join(folder, 'root', 'out', name)])
    }
    const started = Date.now()
    const lap = (label: string) => process.stdout.write(`${PHASE}: ${label} a los ${Math.round((Date.now() - started) / 1000)} s\n`)
    await page.addInitScript(() => {
      delete (window as unknown as { showDirectoryPicker?: unknown }).showDirectoryPicker
    })
    try {
      await page.goto('/')
      await page.locator('input[type=file]').setInputFiles(join(folder, 'root'))
      await expect(page.getByRole('button', { name: /Cargar datos/ })).toBeEnabled({ timeout: 120_000 })
      lap('carpeta leída')
      await page.getByRole('button', { name: /Cargar datos/ }).click()
      await expect(page.locator('.rail')).toBeVisible({ timeout: 240_000 })
      lap('datos cargados')
      if (PHASE === 'sample' || PHASE === 'train') {
        await expect(page.locator('.score-total')).toBeVisible({ timeout: 120_000 })
        process.stdout.write(`${PHASE}: nota en Resumen ${await page.locator('.score-total').innerText()}\n`)
      }
      await page.screenshot({ path: join(tmpdir(), `cuadra-${PHASE}-resumen.png`) })
      await page.keyboard.press('Alt+Digit2')
      await expect(currentTxn(page)).toHaveText(/^BT-\d{4}-\d{7}$/, { timeout: 30_000 })
      lap('primer caso abierto')
      await expect(page.locator('.case-concept')).not.toBeEmpty()
      const latency = await page.evaluate(async () => {
        const store = (window as unknown as { __cuadra: { getState(): { step(d: number): Promise<void>; benchRid: number; caseFor(rid: number): Promise<unknown> } } }).__cuadra
        const times: number[] = []
        for (let i = 0; i < 20; i += 1) {
          await new Promise((resolve) => setTimeout(resolve, 400))
          const begin = performance.now()
          await store.getState().step(1)
          await store.getState().caseFor(store.getState().benchRid)
          times.push(performance.now() - begin)
        }
        return times.sort((a, b) => a - b)
      })
      process.stdout.write(`${PHASE}: siguiente caso con 400 ms de lectura, mediana ${Math.round(latency[10])} ms, peor ${Math.round(latency[19])} ms\n`)
      await page.screenshot({ path: join(tmpdir(), `cuadra-${PHASE}.png`) })
      await page.keyboard.press('u')
      await page.keyboard.press('Alt+Digit3')
      await expect(page.locator('.send-summary')).toBeVisible({ timeout: 200_000 })
      lap('validado')
      if (PHASE === 'live' || PHASE === 'final') {
        await expect(page.locator('.review-only')).toBeVisible()
      } else {
        const button = page.getByRole('button', { name: /Descargar submission/ })
        await expect(button).toBeEnabled({ timeout: 200_000 })
        const [download] = await Promise.all([page.waitForEvent('download'), button.click()])
        expect(download.suggestedFilename()).toBe(`submission_${PHASE}.csv.gz`)
      }
    } finally {
      rmSync(folder, { recursive: true, force: true })
    }
  })
})
