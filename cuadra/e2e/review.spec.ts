import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'

const currentTxn = (page: Page) => page.locator('.case-meta dd').first()

async function openSample(page: Page, sample: 'train' | 'live') {
  await page.goto(`/?demo=${sample}&autoload=1`)
  await expect(page.locator('.topbar')).toBeVisible({ timeout: 30_000 })
}

async function openBench(page: Page) {
  await page.keyboard.press('Alt+Digit3')
  await expect(currentTxn(page)).toBeVisible()
}

async function goToCaseWithProposal(page: Page) {
  for (let i = 0; i < 200; i += 1) {
    const hasRows = await page.locator('.alloc-table').isVisible()
    const hasReason = (await page.locator('.proposal-conf').count()) > 0
    const balanced = (await page.locator('.balance-status').textContent()) === 'Cuadra.'
    if (hasRows && hasReason && balanced) return
    const before = await currentTxn(page).textContent()
    await page.keyboard.press('j')
    await expect(currentTxn(page)).not.toHaveText(before ?? '')
  }
  throw new Error('No se encontró un movimiento con propuesta que cuadre')
}

test('confirms a proposal with Enter and moves to the next movement', async ({ page }) => {
  await openSample(page, 'train')
  await openBench(page)
  await goToCaseWithProposal(page)
  const before = await currentTxn(page).textContent()
  await page.keyboard.press('Enter')
  await expect(currentTxn(page)).not.toHaveText(before ?? '')
  await expect(page.locator('.pace')).toContainText('1 en esta sesión')
})

test('edits an amount with Tab and saves the typed value with Enter', async ({ page }) => {
  await openSample(page, 'train')
  await openBench(page)
  await goToCaseWithProposal(page)
  await page.keyboard.press('Tab')
  await expect(page.locator('.alloc-table input').first()).toBeFocused()
  await page.keyboard.type('12,5')
  await expect(page.locator('.balance-status')).toContainText('Sobran')

  const before = await currentTxn(page).textContent()
  await page.keyboard.press('Enter')
  await expect(page.locator('.hint-strong')).toBeVisible()
  await page.keyboard.press('e')
  await page.keyboard.press('1')
  await expect(page.locator('.chip')).toHaveText(/PARTIAL/)
  await page.keyboard.press('Enter')
  await expect(currentTxn(page)).not.toHaveText(before ?? '')

  await page.keyboard.press('Alt+Digit4')
  const button = page.getByRole('button', { name: /Descargar submission/ })
  await expect(button).toBeEnabled({ timeout: 20_000 })
  const [download] = await Promise.all([page.waitForEvent('download'), button.click()])
  const csv = readFileSync(await download.path(), 'utf8')
  expect(csv).toMatch(new RegExp(`^${before},[^,]+,12\\.50,,PARTIAL$`, 'm'))
})

test('undo brings back the movement with its proposal', async ({ page }) => {
  await openSample(page, 'train')
  await openBench(page)
  await goToCaseWithProposal(page)
  const before = await currentTxn(page).textContent()
  const value = await page.locator('.alloc-table input').first().inputValue()
  await page.keyboard.press('Enter')
  await expect(currentTxn(page)).not.toHaveText(before ?? '')
  await page.keyboard.press('z')
  await expect(currentTxn(page)).toHaveText(before ?? '')
  await expect(page.locator('.alloc-table input').first()).toHaveValue(value)
})

test('assigns a category only without items and marks UNIDENTIFIED', async ({ page }) => {
  await openSample(page, 'train')
  await openBench(page)
  const first = await currentTxn(page).textContent()
  await page.keyboard.press('g')
  await page.keyboard.type('bank')
  await page.keyboard.press('Enter')
  await expect(page.locator('.chip')).toHaveText('BANK_FEES')
  await expect(page.locator('.alloc-table')).toHaveCount(0)
  await page.keyboard.press('Enter')
  await expect(currentTxn(page)).not.toHaveText(first ?? '')

  const second = await currentTxn(page).textContent()
  await page.keyboard.press('u')
  await expect(currentTxn(page)).not.toHaveText(second ?? '')
})

test('finds an item with the search box and adds it', async ({ page }) => {
  await openSample(page, 'train')
  await openBench(page)
  const itemId = (await page.locator('.candidate .mono-small').nth(4).textContent()) ?? ''
  await page.keyboard.press('/')
  await page.keyboard.type(itemId)
  await expect(page.locator('.search-row').first()).toContainText(itemId)
  await page.keyboard.press('Enter')
  await expect(page.locator('.alloc-table')).toContainText(itemId)
})

test('recovers the review after reloading the page', async ({ page }) => {
  await openSample(page, 'train')
  await openBench(page)
  const decided = await currentTxn(page).textContent()
  await page.keyboard.press('u')
  await expect(currentTxn(page)).not.toHaveText(decided ?? '')

  await page.waitForTimeout(300)
  await openSample(page, 'train')
  await expect(page.locator('.notice')).toContainText('Revisión recuperada: 1')
  await expect(page.locator('.legend div', { hasText: 'Revisados a mano' }).locator('dd')).toHaveText('1')
})

test('navigates the queue by keyboard and filters it', async ({ page }) => {
  await openSample(page, 'train')
  await page.keyboard.press('Alt+Digit2')
  const rows = page.locator('.table-queue tbody tr')
  await expect(rows.first()).toHaveClass(/is-cursor/)
  await page.keyboard.press('j')
  await expect(rows.nth(1)).toHaveClass(/is-cursor/)
  const total = await page.locator('.queue-foot .num').textContent()
  await page.getByLabel('Tipo de caso').selectOption('uno_a_n')
  await expect(page.locator('.queue-foot .num')).not.toHaveText(total ?? '')
  await page.locator('.topbar').click()
  await page.keyboard.press('Enter')
  await expect(currentTxn(page)).toBeVisible()
})

test('includes a decision made right before opening the export', async ({ page }) => {
  await openSample(page, 'train')
  await openBench(page)
  const decided = await currentTxn(page).textContent()
  await page.keyboard.press('u')
  await page.keyboard.press('Alt+Digit4')
  await expect(page.locator('.summary div', { hasText: 'Revisados' }).locator('dd')).toHaveText('1', { timeout: 20_000 })
  const button = page.getByRole('button', { name: /Descargar submission/ })
  await expect(button).toBeEnabled()
  const [download] = await Promise.all([page.waitForEvent('download'), button.click()])
  expect(readFileSync(await download.path(), 'utf8')).toContain(`${decided},,`)
})

test('reports an invalid decision with its row and blocks the download', async ({ page }) => {
  await openSample(page, 'train')
  await page.keyboard.press('Alt+Digit4')
  await page.evaluate(() => {
    const store = (window as unknown as { __cuadra: { getState(): { decide(rid: number, d: object): void } } }).__cuadra
    store.getState().decide(12, {
      txnId: 'TXN-00000012',
      allocations: [{ itemId: 'ARI-99999999', amount: 1 }],
      category: null,
      exception: null,
      source: 'manual',
      at: 1,
    })
  })
  await expect(page.locator('.issue-error')).toContainText('ARI-99999999', { timeout: 20_000 })
  await expect(page.getByRole('button', { name: 'Corrige 1 error' })).toBeDisabled()
})

test('exports a valid submission that covers every movement', async ({ page }) => {
  await openSample(page, 'train')
  await page.keyboard.press('Alt+Digit4')
  const button = page.getByRole('button', { name: /Descargar submission/ })
  await expect(button).toBeEnabled({ timeout: 20_000 })
  const [download] = await Promise.all([page.waitForEvent('download'), button.click()])
  await expect(page.locator('.export-main .problem')).toHaveCount(0)
  expect(download.suggestedFilename()).toBe('submission.csv')
  const lines = readFileSync(await download.path(), 'utf8').trim().split('\n')
  expect(lines[0]).toBe('bank_txn_id,item_id,amount,category,exception')
  const txns = new Set(lines.slice(1).map((line) => line.split(',')[0]))
  expect(txns.size).toBe(3000)
  const pairs = lines.slice(1).map((line) => line.split(',').slice(0, 2).join('|'))
  expect(new Set(pairs).size).toBe(pairs.length)
  expect(lines.slice(1).every((line) => /^[^,]+,[^,]*,-?\d+\.\d{2},[^,]*,[^,]*$/.test(line))).toBe(true)
})

test('scores against the golden when there is one', async ({ page }) => {
  await openSample(page, 'train')
  await page.getByRole('button', { name: 'Calcular puntuación' }).click()
  await expect(page.locator('.score-total')).toHaveText(/^0,\d{4}$/, { timeout: 20_000 })
  await expect(page.locator('.note')).toContainText('No es el scorer oficial')
})

test('shows no score at all without a golden', async ({ page }) => {
  await openSample(page, 'live')
  await expect(page.locator('.hero-number')).toBeVisible()
  await expect(page.locator('.score-total')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Calcular puntuación' })).toHaveCount(0)
  await expect(page.locator('.note')).toContainText('no hay puntuación')
})

test('bulk confirmation can be undone', async ({ page }) => {
  await openSample(page, 'train')
  await page.keyboard.press('Alt+Digit2')
  const foot = page.locator('.queue-foot .num')
  await expect(foot).toContainText('3.000')
  const bulk = page.locator('.bulk .btn')
  await bulk.click()
  await bulk.click()
  await expect(page.locator('.notice')).toContainText('Confirmados')
  await expect(foot).not.toContainText('3.000')
  await page.locator('.topbar').click()
  await page.keyboard.press('z')
  await page.keyboard.press('Alt+Digit2')
  await expect(foot).toContainText('3.000', { timeout: 10_000 })
})
