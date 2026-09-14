import { test, expect } from './fixtures'
import { parsedDegree, parseResponse, planResponse, syntheticPdf } from './data'
import type { GenerateResponse } from '../lib/api'

test('overlapping major and minor show one course and retain the unresolved explanation', async ({ page, api }) => {
  const requirements = ['Major', 'Minor'].map((label, index) => ({
    ...parsedDegree.still_needed[0], requirement_id: `req-overlap-${index}`,
    requirement: `Synthetic ${label}`, options: ['CS280'], remaining_quantity: 4, quantity_unit: 'credits' as const,
  }))
  const overlap = "Requirement overlap: CS280 is allocated to 'Synthetic Major'. Sharing with 'Synthetic Minor' is unverified; the course is scheduled and its credits counted once. Confirm the overlap with your advisor."
  const response: GenerateResponse = { ...planResponse, warnings: [overlap],
    semesters: planResponse.semesters.map((semester, index) => ({ ...semester, total_credits: 4, courses: [{
      ...semester.courses[0], slot_id: `slot-overlap-${index}`, requirement: requirements[index], credits: 4,
      course_code: index === 0 ? 'CS280' : 'TBD', title: index === 0 ? 'Synthetic course' : 'Synthetic Minor',
      badge: index === 0 ? 'Required' : 'TBD', credits_estimated: index !== 0,
      credits_note: index === 0 ? '' : 'Unresolved requirement credits; no qualifying course has been selected.',
      catalog_status: index === 0 ? 'present' : 'unresolved', catalog_note: '',
      reason: index === 0 ? 'Required for Synthetic Major' : overlap,
      allocation: { required_quantity: 4, quantity_unit: 'credits', allocated_quantity: index === 0 ? 4 : 0,
        unresolved_quantity: index === 0 ? 0 : 4, status: index === 0 ? 'allocated' : 'partial' },
    }] })),
  }
  api.respond('POST', '/api/plan/parse', { ...parseResponse, parsed: { ...parsedDegree, still_needed: requirements } })
  api.respond('POST', '/api/plan/generate', response)
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByText('CS280', { exact: true })).toHaveCount(1)
  await expect(page.getByText('Unresolved: 4 credits.', { exact: true })).toBeVisible()
  await expect(page.getByText(overlap, { exact: true }).last()).toBeVisible()
  await page.reload()
  await expect(page.getByText(overlap, { exact: true })).toBeVisible()
  await expect(page.getByText('CS280', { exact: true })).toHaveCount(1)
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-plan') || '{}').semesters)
  expect(saved).toEqual(response.semesters)
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
  await expect.poll(() => api.requests('POST', '/api/plan/generate').length).toBe(2)
  await expect(page.getByRole('button', { name: 'Regenerate', exact: true })).toBeEnabled()
  await expect(page.getByText('CS280', { exact: true })).toHaveCount(1)
  await expect(page.getByText(overlap, { exact: true }).last()).toBeVisible()
})
