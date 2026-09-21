import { createHash } from 'node:crypto'
import { test, expect } from './fixtures'
import { parsedDegree, planResponse, syntheticPdf } from './data'

test('uploads a synthetic audit, generates a plan, and restores it on reload', async ({ page, api }) => {
  await page.goto('/scheduler')
  await page.getByRole('link', { name: 'Planner', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Degree Planner', exact: true })).toBeVisible()
  await expect(page.getByText('Upload your DegreeWorks PDF', { exact: true })).toBeVisible()
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await expect(page.getByText('Synthetic Test Student', { exact: true })).toBeVisible()

  expect(api.requests('POST', '/api/plan/parse').map((r) => r.postDataJSON())).toEqual([{
    pdf_base64: syntheticPdf.buffer.toString('base64'),
    client_pdf_hash: createHash('sha256').update(syntheticPdf.buffer).digest('hex'),
  }])
  const elective = page.getByPlaceholder('e.g. CS375, CS445')
  await elective.fill('HUM 101')
  await elective.press('Enter')
  await page.getByRole('spinbutton', { name: 'Custom credits per semester' }).fill('3')
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate').map((r) => r.postDataJSON())).toEqual([{
    parsed_degree: parsedDegree,
    preferences: { courses: ['HUM101'], credits_per_semester: 3, start_term: '202690' },
  }])
  await expect(page.getByText('Programming Language Concepts', { exact: true })).toBeVisible()
  await expect(page.getByText('Writing and Communication', { exact: true })).toBeVisible()
  await expect(page.locator('span.font-bold').filter({ hasText: /^Fall 2026$/ })).toBeVisible()
  await expect(page.locator('span').filter({ hasText: /^Spring 2027$/ })).toHaveCount(2)
  await expect(page.getByText('6 credits', { exact: true })).toBeVisible()
  await expect(page.getByText(planResponse.warnings[0], { exact: true })).toBeVisible()

  await page.reload()
  await expect(page.getByText('Loaded from your last session.', { exact: true })).toBeVisible()
  await expect(page.getByText('Synthetic Test Student', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  await expect(page.getByText('Programming Language Concepts', { exact: true })).toBeVisible()
  await expect(page.getByText('Writing and Communication', { exact: true })).toBeVisible()
  await expect(page.getByText(planResponse.warnings[0], { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/plan/parse')).toHaveLength(1)
  expect(api.requests('POST', '/api/plan/generate')).toHaveLength(1)
})

test('shows a parse error and allows a successful retry', async ({ page, api }) => {
  api.respond('POST', '/api/plan/parse', { detail: 'Synthetic invalid audit' }, 422)
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await expect(page.getByText('Synthetic invalid audit', { exact: true })).toBeVisible()
  api.reset('POST', '/api/plan/parse')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await expect(page.getByText('Synthetic Test Student', { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/plan/parse')).toHaveLength(2)
})

test('shows a generation error and allows a successful retry', async ({ page, api }) => {
  api.respond('POST', '/api/plan/generate', { detail: 'Synthetic service unavailable' }, 503)
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByText('Failed to generate plan. Please try again.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Generate My Plan', exact: true })).toBeEnabled()
  api.reset('POST', '/api/plan/generate')
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  await expect(page.getByText('Failed to generate plan. Please try again.', { exact: true })).toHaveCount(0)
  expect(api.requests('POST', '/api/plan/generate')).toHaveLength(2)
})
