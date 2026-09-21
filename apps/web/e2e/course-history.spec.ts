import { test, expect } from './fixtures'
import { parsedDegree, parseResponse, syntheticPdf } from './data'
import type { CourseAttempt } from '../lib/api'

test('preserves repeated grades and terms through upload, saved state, reload and regeneration', async ({ page, api }) => {
  const history: CourseAttempt[] = [
    { course_code: 'CS100', grade: 'F', credits: 3, term: '2024 Fall', status: 'failed', earns_credit: false,
      source: { document_id: 'a'.repeat(64), line: 4, text: 'CS 100 Synthetic Intro F 3 2024 Fall' } },
    { course_code: 'CS100', grade: 'B+', credits: 3, term: '2025 Spring', status: 'passed', earns_credit: true,
      source: { document_id: 'a'.repeat(64), line: 5, text: 'CS 100 Synthetic Intro B+ 3 2025 Spring' } },
    { course_code: 'CS113', grade: 'T', credits: 3, term: null, status: 'transfer', earns_credit: true, source: null },
  ]
  api.respond('POST', '/api/plan/parse', { ...parseResponse, parsed: { ...parsedDegree, course_attempts: history } })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate')[0].postDataJSON().parsed_degree.course_attempts).toEqual(history)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-parsed') || '{}').parsed.course_attempts)).toEqual(history)
  await page.reload()
  await expect(page.getByText('Loaded from your last session.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
  await expect.poll(() => api.requests('POST', '/api/plan/generate').length).toBe(2)
  expect(api.requests('POST', '/api/plan/generate')[1].postDataJSON().parsed_degree.course_attempts).toEqual(history)
  expect(api.requests('POST', '/api/plan/parse')).toHaveLength(1)
})
