import { test, expect } from './fixtures'
import { solveResponse } from './data'

test('searches courses, submits filters, and renders timed and async meetings', async ({ page, api }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/scheduler$/)
  await expect(page.getByRole('button', { name: 'Solve', exact: true })).toBeDisabled()
  await expect(page.getByText('Click Solve to generate schedules')).toBeVisible()

  const search = page.getByPlaceholder('Search courses… (e.g. CS 280)')
  await search.fill('CS 280')
  await page.getByRole('button', { name: 'CS280 Programming Language Concepts' }).click()
  await search.fill('HUM 101')
  await page.getByRole('button', { name: 'HUM101 Writing and Communication' }).click()
  await page.getByText('Hide Full Sections', { exact: true }).click()
  // These existing selects do not yet have accessible labels (Goal 57).
  await page.getByRole('combobox').nth(0).selectOption('08:00')
  await page.getByRole('combobox').nth(1).selectOption('20:00')
  await page.getByRole('button', { name: 'Solve', exact: true }).click()

  await expect(page.getByText('Schedule 1 / 1', { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/schedule/solve').map((r) => r.postDataJSON())).toEqual([{
    course_codes: ['CS280', 'HUM101'],
    term: '202690',
    options: {
      earliest_start: '08:00', latest_end: '20:00',
      minimize_gaps: false, hide_full_sections: true,
    },
    compact_week: false,
    professor_preferences: {},
  }])
  // One selected-course label plus a block for each of the two meeting patterns.
  await expect(page.getByText('CS280', { exact: true })).toHaveCount(3)
  await expect(page.getByText('Test Lecturer', { exact: true })).toHaveCount(2)
  await expect(page.getByText('12/30', { exact: true })).toHaveCount(2)
  await expect(page.getByText('Async / TBA', { exact: true })).toBeVisible()
  await expect(page.getByText('Test Instructor', { exact: true })).toBeVisible()
  await expect(page.getByText(solveResponse.warnings[0], { exact: true })).toBeVisible()

  await page.reload()
  await expect(page.getByRole('button', { name: 'Solve', exact: true })).toBeEnabled()
  await expect(page.getByText('CS280', { exact: true })).toHaveCount(1)
  await expect(page.getByText('HUM101', { exact: true })).toHaveCount(1)
  await expect(page.getByRole('combobox').nth(0)).toHaveValue('08:00')
  await expect(page.getByText('Click Solve to generate schedules')).toBeVisible()
  expect(api.requests('POST', '/api/schedule/solve')).toHaveLength(1)
})

test('shows a no-results warning and can solve again', async ({ page, api }) => {
  api.respond('POST', '/api/schedule/solve', {
    results: [], warnings: ['No compatible schedules for these test filters.'],
  })
  await page.goto('/scheduler')
  await expect(page.getByRole('button', { name: 'Solve', exact: true })).toBeDisabled()
  await page.getByPlaceholder('Search courses… (e.g. CS 280)').fill('CS280')
  await page.getByRole('button', { name: 'CS280 Programming Language Concepts' }).click()
  await page.getByRole('button', { name: 'Solve', exact: true }).click()
  await expect(page.getByText('No compatible schedules for these test filters.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Solve', exact: true })).toBeEnabled()

  api.respond('POST', '/api/schedule/solve', {
    ...solveResponse,
    results: [{ ...solveResponse.results[0], sections: [solveResponse.results[0].sections[0]], has_async_sections: false }],
  })
  await page.getByRole('button', { name: 'Solve', exact: true }).click()
  await expect(page.getByText('Schedule 1 / 1', { exact: true })).toBeVisible()
  await expect(page.getByText('No compatible schedules for these test filters.')).toHaveCount(0)
  expect(api.requests('POST', '/api/schedule/solve')).toHaveLength(2)
})
