import { test, expect } from './fixtures'
import { syntheticPdf } from './data'

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
