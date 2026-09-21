import { test, expect } from './fixtures'
import { parsedDegree, parseResponse, planResponse, syntheticPdf } from './data'

test('keeps an unreadable option requirement visible and preserves its source after reload', async ({ page, api }, testInfo) => {
  const requirement = {
    ...parsedDegree.still_needed[0], requirement_id: 'req-unreadable-options',
    requirement: 'Advisor elective', options: [], remaining_quantity: 6,
    quantity_unit: 'credits' as const,
    source: { document_id: 'a'.repeat(64), block_index: 1, line: 4,
      text: 'Still needed: 6 Credits in CS490 or advisor-approved work' },
  }
  const reason = "Course options could not be read for 'Advisor elective'. Review the original DegreeWorks requirement with your advisor."
  const course = {
    ...planResponse.semesters[0].courses[0], requirement, course_code: 'TBD',
    title: 'Advisor elective', badge: 'TBD' as const, reason,
    credits_estimated: true, credits_note: 'Credit estimate for an unresolved course.',
    title_status: 'missing' as const, catalog_status: 'unresolved' as const,
    catalog_note: 'No specific catalog course has been selected for this slot.',
  }
  api.respond('POST', '/api/plan/parse', { ...parseResponse, parsed: { ...parsedDegree, still_needed: [requirement] } })
  api.respond('POST', '/api/plan/generate', {
    ...planResponse, semesters: [{ ...planResponse.semesters[0], courses: [course] }], warnings: [reason],
  })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByText(reason, { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Audit requirement: 6 credits.', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByText('Loaded from your last session.', { exact: true })).toBeVisible()
  await expect(page.getByText(reason, { exact: true })).toHaveCount(2)
  await expect(page.getByText(reason, { exact: true }).last()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Regenerate', exact: true })).toBeInViewport({ ratio: 1 })
  await expect(page.getByRole('button', { name: 'Export PDF', exact: true })).toBeInViewport({ ratio: 1 })
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-plan') || '{}').semesters[0].courses[0].requirement))
    .toEqual(requirement)
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
  await expect.poll(() => api.requests('POST', '/api/plan/generate').length).toBe(2)
  expect(api.requests('POST', '/api/plan/generate')[1].postDataJSON().parsed_degree.still_needed).toEqual([requirement])
  await page.screenshot({ path: testInfo.outputPath('unreadable-course-options.png'), fullPage: true })
})
