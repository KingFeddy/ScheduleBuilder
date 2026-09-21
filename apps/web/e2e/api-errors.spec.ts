import { test, expect } from './fixtures'
import { gerCoverage, presentCatalog, syntheticPdf } from './data'

test('shows validation details without confusing course numbers with HTTP status', async ({ page, api }) => {
  api.respond('POST', '/api/plan/generate', { detail: [
    { loc: ['body', 'preferences', 'courses', 0], msg: 'TEST429 is not an eligible course', type: 'value_error', input: 'synthetic-private-input' },
  ] }, 422)
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByText('preferences.courses.0: TEST429 is not an eligible course', { exact: true })).toBeVisible()
  await expect(page.getByText(/Too many requests/)).toHaveCount(0)
  await expect(page.getByText(/synthetic-private-input/)).toHaveCount(0)
})

test('shows a rate-limit retry delay supplied by the API', async ({ page, api }) => {
  api.handle('POST', '/api/plan/parse', async (route) => {
    await route.fulfill({ status: 429, json: { error: 'Rate limit exceeded' }, headers: { 'Retry-After': '30' } })
  })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await expect(page.getByText('Too many requests — try again in 30 seconds.', { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/plan/parse')).toHaveLength(1)
})

test('professor server failures are shown separately from a missing professor', async ({ page, api }) => {
  const path = '/api/professors/Test Lecturer, Taylor'
  api.respond('GET', path, { detail: 'Synthetic unavailable cache' }, 503)
  await page.goto('/scheduler')
  await page.getByPlaceholder('Search courses… (e.g. CS 280)').fill('CS280')
  await page.getByRole('button', { name: 'CS280 Programming Language Concepts' }).click()
  await expect.poll(() => api.requests('GET', path).length).toBeGreaterThan(0)
  await page.getByRole('button', { name: 'Any professor', exact: true }).click()
  await page.getByText('Taylor Test Lecturer', { exact: true }).click()
  const failureMessage = 'Failed to load professor ratings. Close and reopen to try again.'
  await expect(page.getByRole('alert').filter({ hasText: failureMessage })).toHaveText(failureMessage)
  await expect(page.getByText('No ratings found for this professor.', { exact: true })).toHaveCount(0)

  await page.keyboard.press('Escape')
  api.respond('GET', path, { detail: 'Professor not found in RMP cache.' }, 404)
  await page.getByRole('button', { name: 'Any professor', exact: true }).click()
  await page.getByText('Taylor Test Lecturer', { exact: true }).click()
  await expect(page.getByText('No ratings found for this professor.', { exact: true })).toBeVisible()
  await expect(page.getByText(failureMessage, { exact: true })).toHaveCount(0)
})

test('closing GER search cancels its request without showing an application error', async ({ page, api }) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => { release = resolve })
  api.handle('GET', '/api/plan/ger-courses', async (route) => {
    await pending
    await route.fulfill({ status: 503, json: { detail: 'Synthetic late failure' } })
  })
  try {
    await page.goto('/planner')
    await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
    await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
    await page.getByRole('button', { name: 'View available GER Humanities courses →', exact: true }).click()
    await expect.poll(() => api.requests('GET', '/api/plan/ger-courses').length).toBe(1)
    const request = api.requests('GET', '/api/plan/ger-courses')[0]
    await page.keyboard.press('Escape')
    await expect.poll(() => request.failure()?.errorText).toBe('net::ERR_ABORTED')
  } finally {
    release()
  }
  api.respond('GET', '/api/plan/ger-courses', { ...gerCoverage, groups: [{ prefix: 'HUM', courses: [{ ...presentCatalog, code: 'HUM101', title: 'Synthetic GER', title_status: 'verified' }] }] })
  await page.getByRole('button', { name: 'View available GER Humanities courses →', exact: true }).click()
  await expect(page.getByText('Synthetic GER', { exact: true })).toBeVisible()
  await expect(page.getByText('Failed to load GER courses.', { exact: true })).toHaveCount(0)
})
