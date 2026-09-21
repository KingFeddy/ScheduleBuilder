import { test, expect } from './fixtures'
import { parsedDegree, parseResponse, planResponse, syntheticPdf } from './data'

test('cancels earlier generation when a new PDF is uploaded', async ({ page, api }) => {
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  await page.reload()
  let release!: () => void
  const pending = new Promise<void>((resolve) => { release = resolve })
  api.handle('POST', '/api/plan/generate', async (route) => {
    await pending
    await route.fulfill({ json: { ...planResponse, warnings: ['Stale audit response'] } })
  })
  try {
    await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
    await expect.poll(() => api.requests('POST', '/api/plan/generate').length).toBe(2)
    const cancelled = page.waitForEvent('requestfailed', {
      predicate: (request) => new URL(request.url()).pathname === '/api/plan/generate',
    })
    await page.getByRole('button', { name: 'Upload new PDF', exact: true }).click()
    expect((await cancelled).failure()?.errorText).toContain('ERR_ABORTED')
    api.respond('POST', '/api/plan/parse', { ...parseResponse, parsed: { ...parsedDegree, student_name: 'New Synthetic Student' } })
    await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
    await expect(page.getByText('New Synthetic Student', { exact: true })).toBeVisible()
    release()
    await expect(page.getByText('Stale audit response', { exact: true })).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toHaveCount(0)
    expect(await page.evaluate(() => localStorage.getItem('njit-dw-plan'))).toBeNull()
  } finally { release() }
})

test('retains the last valid plan when regeneration returns malformed rows', async ({ page, api }) => {
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  const saved = await page.evaluate(() => localStorage.getItem('njit-dw-plan'))
  api.handle('POST', '/api/plan/generate', async (route) => {
    await route.fulfill({ json: { ...planResponse, semesters: [null] } })
  })
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
  await expect(page.getByText('The generated plan did not preserve your selected requirements. Your previous plan has been kept.', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('njit-dw-plan'))).toBe(saved)
})
