import { test, expect } from './fixtures'
import { courses, presentCatalog, solveResponse } from './data'

test('searches courses, submits filters, and renders timed and async meetings', async ({ page, api }) => {
  api.respond('POST', '/api/schedule/solve', {
    ...solveResponse,
    results: [{ ...solveResponse.results[0], sections: solveResponse.results[0].sections.map((section, i) =>
      i === 0 ? { ...section, meetings: section.meetings.map((meeting, j) => j === 0 ? { ...meeting, days: 'UM' } : meeting) } : section,
    ) }],
  })
  await page.goto('/')
  await expect(page).toHaveURL(/\/scheduler$/)
  await expect(page.getByRole('button', { name: 'Solve', exact: true })).toBeDisabled()
  await expect(page.getByText('Click Solve to generate schedules')).toBeVisible()

  const search = page.getByPlaceholder('Search courses')
  await search.fill('CS 280')
  await page.getByRole('button', { name: 'CS280: Programming Language Concepts' }).click()
  await search.fill('HUM 101')
  await page.getByRole('button', { name: 'HUM101: Writing and Communication' }).click()
  await page.getByText('Hide Full Sections', { exact: true }).click()
  // These existing selects do not yet have accessible labels (Goal 57).
  await page.getByRole('combobox').nth(0).selectOption('08:00')
  await page.getByRole('combobox').nth(1).selectOption('20:00')
  await page.getByRole('button', { name: 'Solve', exact: true }).click()

  await expect(page.getByText('Schedule 1 / 1', { exact: true })).toBeVisible()
  expect(api.requests('GET', '/api/courses').every((r) => new URL(r.url()).searchParams.get('term') === '202690')).toBe(true)
  expect(api.requests('POST', '/api/schedule/solve').map((r) => r.postDataJSON())).toEqual([{
    course_codes: ['CS280', 'HUM101'],
    term: '202690',
    options: {
      earliest_start: '08:00', latest_end: '20:00',
      minimize_gaps: true, hide_full_sections: true,
    },
    compact_week: false,
    professor_preferences: {},
    topic_preferences: {},
  }])
  // One selected-course label plus Sunday, Monday, and Thursday blocks.
  await expect(page.getByText('CS280', { exact: true })).toHaveCount(4)
  await expect(page.getByText('Test Lecturer', { exact: true })).toHaveCount(3)
  await expect(page.getByText('12/30', { exact: true })).toHaveCount(3)
  await expect(page.locator('p').filter({ hasText: /^CS280: Programming Language Concepts$/ })).toBeVisible()
  await expect(page.getByText('Programming Language Concepts', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Writing and Communication', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Sun classes' }).getByText('CS280', { exact: true })).toBeVisible()
  const sundayColumn = page.getByRole('group', { name: 'Sun classes' })
  await expect(sundayColumn).toHaveCSS('height', '720px') // 8am–11pm
  await expect(sundayColumn.locator('div.absolute').filter({ hasText: 'CS280' })).toHaveCSS('top', '48px') // 9am
  const sundayHeader = await page.getByText('Sun', { exact: true }).boundingBox()
  const mondayHeader = await page.getByText('Mon', { exact: true }).boundingBox()
  expect(sundayHeader!.x).toBeLessThan(mondayHeader!.x)
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
    results: [], warnings: ['No compatible schedules for these test filters.'], truncated: false,
  })
  await page.goto('/scheduler')
  await expect(page.getByRole('button', { name: 'Solve', exact: true })).toBeDisabled()
  await page.getByPlaceholder('Search courses').fill('CS280')
  await page.getByRole('button', { name: 'CS280: Programming Language Concepts' }).click()
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

test('lets students select a course with an unknown catalog title', async ({ page, api }) => {
  api.respond('GET', '/api/courses', [{ ...presentCatalog, course_code: 'CS280', title: null, credits: null,
    title_status: 'missing', credits_status: 'missing', credits_min: null, credits_max: null, credits_options: [], metadata_warnings: [] }])
  await page.goto('/scheduler')
  await page.getByPlaceholder('Search courses').fill('CS280')
  await page.getByRole('button', { name: 'CS280: Title unavailable' }).click()
  await expect(page.getByRole('button', { name: 'Solve', exact: true })).toBeEnabled()
  await expect(page.getByText('CS280', { exact: true })).toBeVisible()
})


test('special topics persist, constrain requests, and clear an old schedule on change', async ({ page, api }) => {
  const base = solveResponse.results[0].sections[0]
  const ai = { ...base, course_code: 'CS485', crn: '96523', section_number: '003',
    section_title: 'ST: PHYSICAL AI', topic: 'PHYSICAL AI' }
  const hacking = { ...ai, crn: '91974', section_number: '001',
    section_title: 'ST: Counter Hacking Techniques', topic: 'Counter Hacking Techniques' }
  api.respond('GET', '/api/courses', [{ ...courses[0], course_code: 'CS485', title: 'St: Physical Ai' }])
  api.respond('GET', '/api/courses/CS485/sections', [ai, hacking])
  api.respond('POST', '/api/schedule/solve', {
    results: [{ ...solveResponse.results[0], sections: [ai] }], warnings: [], truncated: false,
  })
  await page.goto('/scheduler')
  await page.getByPlaceholder('Search courses').fill('CS 485')
  await page.getByRole('button', { name: 'CS485: Special Topics' }).click()
  const picker = page.getByRole('combobox', { name: 'Topic for CS485' })
  await expect(picker).toBeVisible()
  await expect(page.getByRole('button', { name: 'Solve', exact: true })).toBeDisabled()
  await picker.selectOption('PHYSICAL AI')
  await page.getByRole('button', { name: 'Solve', exact: true }).click()
  await expect(page.getByText('Schedule 1 / 1', { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/schedule/solve')[0].postDataJSON().topic_preferences).toEqual({ CS485: 'PHYSICAL AI' })
  await expect(page.locator('p').filter({ hasText: /^Physical AI$/ })).toHaveCount(0)
  await picker.selectOption('Counter Hacking Techniques')
  await expect(page.getByText('Click Solve to generate schedules')).toBeVisible()
  await page.reload()
  await expect(picker).toHaveValue('Counter Hacking Techniques')
  await expect(page.getByRole('button', { name: 'Solve', exact: true })).toBeEnabled()
})
