import { test, expect } from './fixtures'
import { parsedDegree, parseResponse, planResponse, syntheticPdf } from './data'

test('shows a specific elective validation error and permits correction', async ({ page, api }) => {
  api.respond('POST', '/api/plan/generate', { detail: [{
    loc: ['body', 'preferences', 'courses', 0],
    msg: 'Value error, Use one specific course code such as CS435 per entry.', type: 'value_error',
  }] }, 422)
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  const elective = page.getByPlaceholder('e.g. CS375, CS445', { exact: true })
  await elective.fill('CS400,CS401')
  await elective.press('Enter')
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByText('preferences.courses.0: Value error, Use one specific course code such as CS435 per entry.', { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate')[0].postDataJSON().preferences.courses).toEqual(['CS400,CS401'])
  await expect(page.getByRole('heading', { name: 'Your Academic Plan', exact: true })).toHaveCount(0)
  await page.getByText('CS400,CS401', { exact: true }).getByRole('button').click()
  await elective.fill('cs435')
  await elective.press('Enter')
  api.respond('POST', '/api/plan/generate', planResponse)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan', exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate')[1].postDataJSON().preferences.courses).toEqual(['CS435'])
  await expect(page.getByText(/Use one specific course code/)).toHaveCount(0)
})

test('accepts a completed degree without requiring a remaining course', async ({ page, api }) => {
  const completed = { ...parsedDegree, credits_completed: 120, credits_remaining: 0, still_needed: [], in_progress_courses: [] }
  const message = "You've completed all degree requirements. Congratulations!"
  api.respond('POST', '/api/plan/parse', { ...parseResponse, parsed: completed })
  api.respond('POST', '/api/plan/generate', { semesters: [], projected_graduation: 'This semester', warnings: [message] })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByText(message, { exact: true })).toBeVisible()
  await expect(page.getByText('This semester', { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate')[0].postDataJSON().parsed_degree).toEqual(completed)
})
