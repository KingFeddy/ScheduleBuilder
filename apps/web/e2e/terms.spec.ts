import { test, expect } from './fixtures'
import { sectionList, solveResponse, termDiscovery } from './data'

const springDefault = { ...termDiscovery, default_term: '202710' }

test('uses the API default across section lookup and solve during year rollover', async ({ page, api }, testInfo) => {
  api.respond('GET', '/api/terms', springDefault)
  api.respond('POST', '/api/schedule/solve', {
    ...solveResponse,
    results: solveResponse.results.map((result) => ({ ...result,
      sections: result.sections.map((section) => ({ ...section, term: '202710' })),
    })),
  })
  await page.goto('/scheduler')
  await expect(page.getByLabel('Semester', { exact: true })).toHaveValue('')
  await expect(page.getByRole('option', { name: 'Default: Spring 2027', exact: true })).toBeAttached()
  await page.getByPlaceholder('Search courses… (e.g. CS 280)').fill('CS280')
  await page.getByRole('button', { name: 'CS280 Programming Language Concepts' }).click()
  await page.getByRole('button', { name: 'Solve', exact: true }).click()
  expect(api.requests('POST', '/api/schedule/solve')[0].postDataJSON().term).toBe('202710')
  for (const path of ['/api/courses/CS280/sections']) {
    await expect.poll(() => api.requests('GET', path).length).toBeGreaterThan(0)
    expect(api.requests('GET', path).every((r) => new URL(r.url()).searchParams.get('term') === '202710')).toBe(true)
  }
  await page.screenshot({ path: testInfo.outputPath('semester-selection.png'), fullPage: true })
})

test('keeps a default with no data visible and requires a collected semester before solving', async ({ page, api }, testInfo) => {
  api.respond('GET', '/api/terms', {
    default_term: '202710', terms: [
      { code: '202690', label: 'Fall 2026', has_data: true },
      { code: '202710', label: 'Spring 2027', has_data: false },
    ],
  })
  await page.goto('/scheduler')
  await expect(page.getByText('No section data has been collected for Spring 2027.', { exact: true })).toBeVisible()
  await page.getByPlaceholder('Search courses… (e.g. CS 280)').fill('CS280')
  await page.getByRole('button', { name: 'CS280 Programming Language Concepts' }).click()
  await expect(page.getByRole('button', { name: 'Solve', exact: true })).toBeDisabled()
  expect(api.requests('GET', '/api/courses/CS280/sections')).toHaveLength(0)
  expect(api.requests('POST', '/api/schedule/solve')).toHaveLength(0)
  await page.screenshot({ path: testInfo.outputPath('semester-no-data.png'), fullPage: true })
  await page.getByLabel('Semester', { exact: true }).selectOption('202690')
  await page.getByRole('button', { name: 'Solve', exact: true }).click()
  expect(api.requests('POST', '/api/schedule/solve')[0].postDataJSON().term).toBe('202690')
})

test('retries failed discovery without using the legacy cached default', async ({ page, api }) => {
  await page.addInitScript(() => {
    try {
      if (!localStorage.getItem('njit-scheduler')) localStorage.setItem('njit-scheduler', JSON.stringify({
        version: 7, state: { selectedCourses: ['CS280'], term: '202690', professorPreferences: { CS280: ['Old Instructor'] } },
      }))
    } catch (error) { throw new Error('Could not seed synthetic storage', { cause: error }) }
  })
  api.respond('GET', '/api/terms', { detail: 'Synthetic term outage' }, 503)
  await page.goto('/scheduler')
  await expect(page.getByText('Semester information is unavailable.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Solve', exact: true })).toBeDisabled()
  expect(api.requests('GET', '/api/courses/CS280/sections')).toHaveLength(0)
  expect(api.requests('GET', '/api/catalog/coverage')).toHaveLength(0)
  api.respond('GET', '/api/terms', springDefault)
  await page.getByRole('button', { name: 'Retry semesters', exact: true }).click()
  await page.getByRole('button', { name: 'Solve', exact: true }).click()
  const body = api.requests('POST', '/api/schedule/solve')[0].postDataJSON()
  expect(body.term).toBe('202710')
  expect(body.professor_preferences).toEqual({})
})

test('changing semesters clears displayed results and cancels a solve from the previous selection', async ({ page, api }) => {
  await page.goto('/scheduler')
  await page.getByPlaceholder('Search courses… (e.g. CS 280)').fill('CS280')
  await page.getByRole('button', { name: 'CS280 Programming Language Concepts' }).click()
  await page.getByRole('button', { name: 'Solve', exact: true }).click()
  await expect(page.getByText('Schedule 1 / 1', { exact: true })).toBeVisible()
  let release!: () => void
  const pending = new Promise<void>((resolve) => { release = resolve })
  api.handle('POST', '/api/schedule/solve', async (route) => {
    await pending
    await route.fulfill({ json: { ...solveResponse, warnings: ['Stale semester response'] } })
  })
  try {
    await page.getByRole('button', { name: 'Solve', exact: true }).click()
    await expect.poll(() => api.requests('POST', '/api/schedule/solve').length).toBe(2)
    const request = api.requests('POST', '/api/schedule/solve')[1]
    await page.getByLabel('Semester', { exact: true }).selectOption('202710')
    await expect(page.getByText('Schedule 1 / 1', { exact: true })).toHaveCount(0)
    await expect.poll(() => request.failure()?.errorText).toBe('net::ERR_ABORTED')
    await page.getByLabel('Semester', { exact: true }).selectOption('202690')
  } finally { release() }
  api.reset('POST', '/api/schedule/solve')
  await page.getByRole('button', { name: 'Solve', exact: true }).click()
  await expect(page.getByText('Schedule 1 / 1', { exact: true })).toBeVisible()
  await expect(page.getByText('Stale semester response', { exact: true })).toHaveCount(0)
})

test('cancels old section lookups and fetches again when returning to a semester', async ({ page, api }) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => { release = resolve })
  api.handle('GET', '/api/courses/CS280/sections', async (route) => {
    if (new URL(route.request().url()).searchParams.get('term') === '202690') await pending
    await route.fulfill({ json: [sectionList[0]] })
  })
  await page.goto('/scheduler')
  await page.getByPlaceholder('Search courses… (e.g. CS 280)').fill('CS280')
  await page.getByRole('button', { name: 'CS280 Programming Language Concepts' }).click()
  try {
    await expect.poll(() => api.requests('GET', '/api/courses/CS280/sections').length).toBe(1)
    const first = api.requests('GET', '/api/courses/CS280/sections')[0]
    await page.getByLabel('Semester', { exact: true }).selectOption('202710')
    await expect.poll(() => first.failure()?.errorText).toBe('net::ERR_ABORTED')
  } finally { release() }
  api.reset('GET', '/api/courses/CS280/sections')
  await expect.poll(() => api.requests('GET', '/api/courses/CS280/sections').length).toBe(2)
  await page.getByLabel('Semester', { exact: true }).selectOption('202690')
  await expect.poll(() => api.requests('GET', '/api/courses/CS280/sections').length).toBe(3)
})

test('leaving during a solve cancels it and allows solving after returning', async ({ page, api }) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => { release = resolve })
  api.handle('POST', '/api/schedule/solve', async (route) => {
    await pending
    await route.fulfill({ json: solveResponse })
  })
  await page.goto('/scheduler')
  await page.getByPlaceholder('Search courses… (e.g. CS 280)').fill('CS280')
  await page.getByRole('button', { name: 'CS280 Programming Language Concepts' }).click()
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
