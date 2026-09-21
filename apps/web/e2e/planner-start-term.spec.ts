import { test, expect } from './fixtures'
import { planResponse, syntheticPdf, termDiscovery } from './data'
import { planningTermLabel } from '../lib/planner-terms'

test('saves a chosen future start and keeps it when the server default changes', async ({ page, api }, testInfo) => {
  api.handle('POST', '/api/plan/generate', async (route) => {
    const start = route.request().postDataJSON().preferences.start_term as string
    const next = start.endsWith('10') ? `${start.slice(0, 4)}90` : `${Number(start.slice(0, 4)) + 1}10`
    await route.fulfill({ json: { ...planResponse,
      semesters: planResponse.semesters.map((semester, index) => ({ ...semester,
        term: index ? next : start, term_label: planningTermLabel(index ? next : start),
      })), projected_graduation: planningTermLabel(next),
    } })
  })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByLabel('Start semester', { exact: true }).selectOption('203190')
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByText('Plan start: Fall 2031', { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate')[0].postDataJSON().preferences.start_term).toBe('203190')
  api.respond('GET', '/api/terms', { ...termDiscovery, default_term: '202710' })
  await page.reload()
  await expect(page.getByLabel('Start semester', { exact: true })).toHaveValue('203190')
  await expect(page.getByText('Plan start: Fall 2031', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
  await expect.poll(() => api.requests('POST', '/api/plan/generate').length).toBe(2)
  expect(api.requests('POST', '/api/plan/generate')[1].postDataJSON().preferences.start_term).toBe('203190')
  await expect(page.getByRole('button', { name: 'Regenerate', exact: true })).toBeEnabled()
  await page.getByLabel('Start semester', { exact: true }).selectOption('202610')
  // Editing the next request must not relabel the existing saved plan.
  await expect(page.getByText('Plan start: Fall 2031', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
  await expect(page.getByText('Plan start: Spring 2026', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByText('Plan start: Spring 2026', { exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('start-semester.png'), fullPage: true })
})

test('does not guess a default when discovery fails and allows retry', async ({ page, api }) => {
  api.respond('GET', '/api/terms', { detail: 'Synthetic unavailable terms' }, 503)
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await expect(page.getByRole('button', { name: 'Generate My Plan', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Retry semester lookup' })).toBeVisible()
  api.reset('GET', '/api/terms')
  await page.getByRole('button', { name: 'Retry semester lookup' }).click()
  await expect(page.getByRole('button', { name: 'Generate My Plan', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByText('Plan start: Fall 2026', { exact: true })).toBeVisible()
})
