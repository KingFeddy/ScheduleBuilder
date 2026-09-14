import { test, expect } from './fixtures'
import { parsedDegree, parseResponse, planResponse, syntheticPdf } from './data'

test('keeps separate requirement identities and quantities through reload and regeneration', async ({ page, api }, testInfo) => {
  const requirements = [
    { requirement_id: 'req-major', requirement: 'Major choice', options: ['CS280'],
      remaining_quantity: 2, quantity_unit: 'classes' as const, quantity_status: 'known' as const,
      source: { document_id: 'a'.repeat(64), block_index: 1, line: 4, text: 'Still needed: 2 Classes in CS 280' } },
    { requirement_id: 'req-minor', requirement: 'Minor choice', options: ['CS280'],
      remaining_quantity: null, quantity_unit: 'credits' as const, quantity_status: 'unresolved' as const,
      source: { document_id: 'a'.repeat(64), block_index: 2, line: 6, text: 'Still needed: ? Credits in CS 280' } },
  ]
  const degree = { ...parsedDegree, still_needed: requirements }
  const courses = requirements.map((requirement, index) => ({
    ...planResponse.semesters[0].courses[0], slot_id: `slot-${index}`, requirement,
  }))
  const response = { ...planResponse, semesters: [{ ...planResponse.semesters[0], courses, total_credits: 6 }] }
  api.respond('POST', '/api/plan/parse', { ...parseResponse, parsed: degree })
  api.respond('POST', '/api/plan/generate', response)
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByText('Audit requirement: 2 classes.', { exact: true })).toBeVisible()
  await expect(page.getByText('Audit requirement quantity is unknown.', { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate')[0].postDataJSON().parsed_degree.still_needed).toEqual(requirements)
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-plan') || '{}'))
  expect(stored.semesters[0].courses).toEqual(courses)
  await page.reload()
  await expect(page.getByText('Audit requirement: 2 classes.', { exact: true })).toBeVisible()
  await expect(page.getByText('Audit requirement quantity is unknown.', { exact: true })).toBeVisible()
  api.respond('POST', '/api/plan/generate', { ...response, semesters: [{ ...response.semesters[0], courses: [...courses].reverse() }] })
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
  await expect.poll(async () => page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-plan') || '{}').semesters?.[0]?.courses))
    .toEqual([...courses].reverse())
  await expect(page.getByText('Audit requirement: 2 classes.', { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate')[1].postDataJSON().parsed_degree.still_needed).toEqual(requirements)
  await page.screenshot({ path: testInfo.outputPath('requirement-quantities.png'), fullPage: true })
})

test('requires regeneration for legacy duplicate rows without source binding', async ({ page }) => {
  const course = { ...planResponse.semesters[0].courses[0] }
  Reflect.deleteProperty(course, 'slot_id')
  Reflect.deleteProperty(course, 'requirement')
  await page.addInitScript(({ parsed, plan }) => {
    try {
      localStorage.setItem('njit-dw-parsed', JSON.stringify(parsed))
      localStorage.setItem('njit-dw-plan', JSON.stringify(plan))
    } catch { /* Synthetic storage only. */ }
  }, { parsed: parsedDegree, plan: { graduation: 'Fall 2026', semesters: [{ ...planResponse.semesters[0], courses: [course, course] }] } })
  await page.goto('/planner')
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toHaveCount(0)
  await expect(page.getByText('Your saved plan is outdated, damaged, or belongs to a different audit. Generate a new plan to continue.', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-plan') || '{}').semesters[0].courses))
    .toEqual([course, course])
})
