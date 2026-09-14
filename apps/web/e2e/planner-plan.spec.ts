import { test, expect } from './fixtures'
import { parsedDegree, parseResponse, planResponse, syntheticPdf } from './data'

const notice = 'Your saved plan is outdated, damaged, or belongs to a different audit. Generate a new plan to continue.'

for (const mode of ['mismatched', 'damaged'] as const) {
  test(`recovers from a ${mode} saved plan without discarding the current audit`, async ({ page, api }, testInfo) => {
    const saved = { version: 1, source_audit: mode === 'mismatched' ? { ...parsedDegree, credits_remaining: 9 } : parsedDegree,
      semesters: mode === 'damaged' ? [{ ...planResponse.semesters[0], courses: [null] }] : planResponse.semesters,
      graduation: planResponse.projected_graduation, warnings: planResponse.warnings,
    }
    await page.addInitScript(({ audit, plan }) => {
      try {
        if (!localStorage.getItem('njit-dw-parsed')) {
          localStorage.setItem('njit-dw-parsed', JSON.stringify(audit))
          localStorage.setItem('njit-dw-plan', JSON.stringify(plan))
        }
      } catch { /* Synthetic browser data. */ }
    }, { audit: parsedDegree, plan: saved })
    await page.goto('/planner')
    await expect(page.getByText('Synthetic Test Student', { exact: true })).toBeVisible()
    await expect(page.getByText(notice, { exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toHaveCount(0)
    if (mode === 'mismatched') await page.screenshot({ path: testInfo.outputPath('saved-plan-recovery.png'), fullPage: true })
    await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
    await expect(page.getByText(notice, { exact: true })).toHaveCount(0)
    expect(api.requests('POST', '/api/plan/parse')).toHaveLength(0)
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-plan') || '{}').source_audit)).toEqual(parsedDegree)
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  })
}

test('ignores an earlier audit response after a new PDF is uploaded', async ({ page, api }) => {
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
    await page.getByRole('button', { name: 'Upload new PDF', exact: true }).click()
    api.respond('POST', '/api/plan/parse', { ...parseResponse, parsed: { ...parsedDegree, student_name: 'New Synthetic Student' } })
    await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
    await expect(page.getByText('New Synthetic Student', { exact: true })).toBeVisible()
    const response = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/plan/generate')
    release()
    await (await response).finished()
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
  await expect(page.getByText('Could not regenerate the plan. Please try again.', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('njit-dw-plan'))).toBe(saved)
})
