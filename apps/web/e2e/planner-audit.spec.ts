import { test, expect } from './fixtures'
import { parsedDegree, planResponse, syntheticPdf } from './data'

const recoveryNotice = 'Your saved audit is incomplete or uses an unsupported format. Upload your DegreeWorks PDF again to refresh the requirements.'

for (const savedAudit of [
  { ...parsedDegree, still_needed: [{ requirement: 'Synthetic requirement', options: ['CS280'] }] },
  { ...parsedDegree, majors: null },
]) {
  test(`requires a fresh upload for an incompatible saved audit (${savedAudit.majors === null ? 'damaged' : 'legacy'})`, async ({ page, api }) => {
    await page.addInitScript(({ audit, plan }) => {
      try {
        if (!localStorage.getItem('njit-dw-parsed')) {
          localStorage.setItem('njit-dw-parsed', JSON.stringify(audit))
          localStorage.setItem('njit-dw-plan', JSON.stringify(plan))
        }
      } catch { /* Synthetic browser storage. */ }
    }, { audit: savedAudit, plan: { semesters: planResponse.semesters, graduation: 'Spring 2025', warnings: ['Old plan warning'] } })
    await page.goto('/planner')
    await expect(page.getByText(recoveryNotice, { exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Generate My Plan', exact: true })).toHaveCount(0)
    expect(api.requests('POST', '/api/plan/generate')).toHaveLength(0)
    // Invalid saved data stays available until the user replaces it.
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-parsed') || '{}'))).toEqual(savedAudit)
    await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
    await expect(page.getByText('Synthetic Test Student', { exact: true })).toBeVisible()
    await expect(page.getByText(recoveryNotice, { exact: true })).toHaveCount(0)
    expect(await page.evaluate(() => localStorage.getItem('njit-dw-plan'))).toBeNull()
    await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
    expect(api.requests('POST', '/api/plan/generate')[0].postDataJSON().parsed_degree).toEqual(parsedDegree)
    await page.reload()
    await expect(page.getByText('Loaded from your last session.', { exact: true })).toBeVisible()
    await expect(page.getByText(planResponse.warnings[0], { exact: true })).toBeVisible()
  })
}

test('upgrades a compatible saved audit while retaining explicitly unresolved quantities', async ({ page, api }, testInfo) => {
  const audit = { ...parsedDegree, still_needed: [{ ...parsedDegree.still_needed[0],
    remaining_quantity: null, quantity_unit: 'unknown', quantity_status: 'unresolved',
  }] }
  await page.addInitScript((audit) => {
    try {
      if (!localStorage.getItem('njit-dw-parsed')) localStorage.setItem('njit-dw-parsed', JSON.stringify(audit))
    } catch { /* Synthetic browser storage. */ }
  }, audit)
  await page.goto('/planner')
  await expect(page.getByText('Loaded from your last session.', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-parsed') || '{}'))).toEqual({ version: 1, parsed: audit })
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate')[0].postDataJSON().parsed_degree).toEqual(audit)
  await page.screenshot({ path: testInfo.outputPath('compatible-audit.png'), fullPage: true })
})

test('rejects an incomplete parse response before saving it or enabling generation', async ({ page, api }) => {
  api.handle('POST', '/api/plan/parse', async (route) => {
    await route.fulfill({ json: { parsed: { ...parsedDegree, still_needed: [null] }, server_hash: 'a'.repeat(64), warnings: [] } })
  })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await expect(page.getByText('The planner returned incomplete audit data. Please try uploading your DegreeWorks PDF again.', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('njit-dw-parsed'))).toBeNull()
  await expect(page.getByRole('button', { name: 'Generate My Plan', exact: true })).toHaveCount(0)
  api.reset('POST', '/api/plan/parse')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await expect(page.getByText('Synthetic Test Student', { exact: true })).toBeVisible()
})
