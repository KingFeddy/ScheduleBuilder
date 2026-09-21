import { test, expect } from './fixtures'
import { parsedDegree, syntheticPdf } from './data'

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
