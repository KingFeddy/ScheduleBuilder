import { test, expect } from './fixtures'
import { solveResponse, termDiscovery } from './data'

const springDefault = { ...termDiscovery, default_term: '202710' }

test('fixes the displayed semester, section lookups, and solves to Fall 2026 despite saved choices and API defaults', async ({ page, api }, testInfo) => {
  await page.addInitScript(() => {
    if (!localStorage.getItem('njit-scheduler')) localStorage.setItem('njit-scheduler', JSON.stringify({
      version: 8, state: { selectedCourses: ['CS280'], term: '202710', preferredTerm: '202710',
        professorPreferences: { CS280: ['Old Instructor'] } },
    }))
  })
  api.respond('GET', '/api/terms', springDefault)
  await page.goto('/scheduler')
  await expect(page.getByText('Current semester', { exact: true })).toBeVisible()
  await expect(page.getByText('Fall 2026', { exact: true })).toBeVisible()
  await expect(page.getByRole('combobox')).toHaveCount(2) // Only the class-hour filters remain.
  await page.getByRole('button', { name: 'Solve', exact: true }).click()
  await expect(page.getByText('Schedule 1 / 1', { exact: true })).toBeVisible()
  const body = api.requests('POST', '/api/schedule/solve')[0].postDataJSON()
  expect(body.term).toBe('202690')
  expect(body.professor_preferences).toEqual({})
  await expect.poll(() => api.requests('GET', '/api/courses/CS280/sections').length).toBeGreaterThan(0)
  expect(api.requests('GET', '/api/courses/CS280/sections').every((r) => new URL(r.url()).searchParams.get('term') === '202690')).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('fixed-semester.png'), fullPage: true })
  await page.reload()
  await expect(page.getByText('Fall 2026', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Solve', exact: true }).click()
  await expect(page.getByText('Schedule 1 / 1', { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/schedule/solve').every((r) => r.postDataJSON().term === '202690')).toBe(true)
})

test('does not fall back to another semester when Fall 2026 has no data', async ({ page, api }) => {
  api.respond('GET', '/api/terms', {
    default_term: '202710', terms: [
      { code: '202690', label: 'Fall 2026', has_data: false },
      { code: '202710', label: 'Spring 2027', has_data: true },
    ],
  })
  await page.goto('/scheduler')
  await expect(page.getByText('No section data has been collected for Fall 2026. Please try again later.', { exact: true })).toBeVisible()
  await page.getByPlaceholder('Search courses').fill('CS280')
  await expect(page.getByRole('button', { name: 'CS280: Programming Language Concepts' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Solve', exact: true })).toBeDisabled()
  expect(api.requests('GET', '/api/courses/CS280/sections')).toHaveLength(0)
  expect(api.requests('POST', '/api/schedule/solve')).toHaveLength(0)
})

test('keeps Fall 2026 visible during discovery failures and supports retry', async ({ page, api }) => {
  await page.addInitScript(() => {
    localStorage.setItem('njit-scheduler', JSON.stringify({
      version: 8, state: { selectedCourses: ['CS280'], term: '202710', preferredTerm: '202710',
        professorPreferences: { CS280: ['Old Instructor'] } },
    }))
  })
  api.respond('GET', '/api/terms', { detail: 'Synthetic term outage' }, 503)
  await page.goto('/scheduler')
  await expect(page.getByText('Fall 2026', { exact: true })).toBeVisible()
  await expect(page.getByText('Semester information is unavailable.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Solve', exact: true })).toBeDisabled()
  expect(api.requests('GET', '/api/courses/CS280/sections')).toHaveLength(0)
  api.respond('GET', '/api/terms', springDefault)
  await page.getByRole('button', { name: 'Retry', exact: true }).click()
  await page.getByRole('button', { name: 'Solve', exact: true }).click()
  await expect(page.getByText('Schedule 1 / 1', { exact: true })).toBeVisible()
  const body = api.requests('POST', '/api/schedule/solve')[0].postDataJSON()
  expect(body.term).toBe('202690')
  expect(body.professor_preferences).toEqual({})
})

test('leaving during a solve cancels it and allows solving after returning', async ({ page, api }) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => { release = resolve })
  api.handle('POST', '/api/schedule/solve', async (route) => {
    await pending
    await route.fulfill({ json: solveResponse })
  })
  await page.goto('/scheduler')
  await page.getByPlaceholder('Search courses').fill('CS280')
  await page.getByRole('button', { name: 'CS280: Programming Language Concepts' }).click()
  try {
    await page.getByRole('button', { name: 'Solve', exact: true }).click()
    await expect.poll(() => api.requests('POST', '/api/schedule/solve').length).toBe(1)
    const request = api.requests('POST', '/api/schedule/solve')[0]
    await page.getByRole('link', { name: 'Planner', exact: true }).click()
    await expect.poll(() => request.failure()?.errorText).toBe('net::ERR_ABORTED')
  } finally { release() }
  api.reset('POST', '/api/schedule/solve')
  await page.getByRole('link', { name: 'Scheduler', exact: true }).click()
  await page.getByRole('button', { name: 'Solve', exact: true }).click()
  await expect(page.getByText('Schedule 1 / 1', { exact: true })).toBeVisible()
})
