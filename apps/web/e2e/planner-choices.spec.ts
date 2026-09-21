import { test, expect } from './fixtures'
import { parsedDegree, parseResponse, planResponse, courses, replacementCourse, replacementPlan, syntheticPdf } from './data'

const choices = { 'req-writing': ['HUM201'] }

test('saves recalculated replacements, retains them in both generate actions, and resets them', async ({ page, api }, testInfo) => {
  api.respond('GET', '/api/courses', [replacementCourse, courses[0]])
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await page.getByRole('button', { name: 'swap →', exact: true }).click()
  await expect(page.getByRole('dialog').getByText('CS280', { exact: true })).toHaveCount(0)
  api.respond('POST', '/api/plan/generate', replacementPlan)
  await page.getByRole('button', { name: 'HUM201 Replacement', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByText('7 credits', { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate')[1].postDataJSON().preferences.requirement_choices).toEqual(choices)
  await page.reload()
  await expect(page.getByText('Replacement', { exact: true })).toBeVisible()
  for (const name of ['Generate My Plan', 'Regenerate']) {
    const count = api.requests('POST', '/api/plan/generate').length
    await page.getByRole('button', { name, exact: true }).click()
    await expect.poll(() => api.requests('POST', '/api/plan/generate').length).toBe(count + 1)
    await expect(page.getByRole('button', { name: 'Regenerate', exact: true })).toBeEnabled()
    expect(api.requests('POST', '/api/plan/generate').at(-1)!.postDataJSON().preferences.requirement_choices).toEqual(choices)
  }
  await page.screenshot({ path: testInfo.outputPath('recalculated-choice.png'), fullPage: true })
  api.reset('POST', '/api/plan/generate')
  await page.getByRole('button', { name: 'Reset course choices', exact: true }).click()
  await expect(page.getByText('Writing and Communication', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Reset course choices', exact: true })).toHaveCount(0)
  expect(api.requests('POST', '/api/plan/generate').at(-1)!.postDataJSON().preferences.requirement_choices).toBeUndefined()
})

test('failed and mismatched replacements preserve the saved plan and allow retry', async ({ page, api }) => {
  api.respond('GET', '/api/courses', [replacementCourse])
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByText('Writing and Communication', { exact: true })).toBeVisible()
  const saved = await page.evaluate(() => localStorage.getItem('njit-dw-plan'))
  await page.getByRole('button', { name: 'swap →', exact: true }).click()
  api.respond('POST', '/api/plan/generate', { detail: 'Synthetic requirement mismatch' }, 422)
  await page.getByRole('button', { name: 'HUM201 Replacement', exact: true }).click()
  await expect(page.getByRole('dialog').getByRole('alert')).toHaveText('Synthetic requirement mismatch')
  expect(await page.evaluate(() => localStorage.getItem('njit-dw-plan'))).toBe(saved)
  api.reset('POST', '/api/plan/generate') // An old API silently ignoring the submitted choice.
  await page.getByRole('button', { name: 'HUM201 Replacement', exact: true }).click()
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('did not preserve your selected requirements')
  expect(await page.evaluate(() => localStorage.getItem('njit-dw-plan'))).toBe(saved)
  api.respond('POST', '/api/plan/generate', replacementPlan)
  await page.getByRole('button', { name: 'HUM201 Replacement', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByText('Replacement', { exact: true })).toBeVisible()
})

test('cancelled swaps cannot overwrite a later generation and all generate controls share the pending state', async ({ page, api }) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => { release = resolve })
  api.respond('GET', '/api/courses', [replacementCourse])
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await page.getByRole('button', { name: 'swap →', exact: true }).click()
  api.handle('POST', '/api/plan/generate', async (route) => {
    await pending
    await route.fulfill({ json: replacementPlan })
  })
  try {
    await page.getByRole('button', { name: 'HUM201 Replacement', exact: true }).click()
    await expect(page.getByText('Recalculating your plan… Close to cancel.')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Regenerate', exact: true })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Generating your plan…', exact: true })).toBeDisabled()
    await page.getByRole('button', { name: 'Cancel replacement', exact: true }).click()
    await expect(page.getByText('Writing and Communication', { exact: true })).toBeVisible()
    api.reset('POST', '/api/plan/generate')
    await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Regenerate', exact: true })).toBeEnabled()
  } finally { release() }
  await expect(page.getByText('Replacement', { exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-plan') || '{}').requirementChoices)).toBeUndefined()
})

test('all subjects and later catalog pages are searched against the actual requirement', async ({ page, api }) => {
  const requirement = { ...parsedDegree.still_needed[0], options: ['CS3XX'] }
  const audit = { ...parsedDegree, still_needed: [requirement], completed_courses: ['CS300'] }
  api.respond('POST', '/api/plan/parse', { ...parseResponse, parsed: audit })
  api.respond('POST', '/api/plan/generate', { ...planResponse, semesters: [{ ...planResponse.semesters[0], courses: [
    { ...planResponse.semesters[0].courses[0], course_code: 'TBD', badge: 'TBD', requirement },
  ] }] })
  api.handle('GET', '/api/courses', async (route) => {
    const params = new URL(route.request().url()).searchParams
    expect(params.get('subject')).toBe('CS')
    await route.fulfill({ json: params.get('page') === '1'
      ? Array.from({ length: 100 }, (_, i) => ({ ...courses[0], course_code: `CS${100 + i}` }))
      : [{ ...courses[0], course_code: 'CS300' }, { ...courses[0], course_code: 'CS301' }] })
  })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await page.getByRole('button', { name: 'choose →', exact: true }).click()
  await expect(page.getByRole('button', { name: 'CS301 Programming Language Concepts' })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'CS300 Programming Language Concepts' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'CS100 Programming Language Concepts' })).toHaveCount(0)
})

test('replaces the matching general elective preference without readding the old course', async ({ page, api }, testInfo) => {
  api.respond('GET', '/api/courses', [replacementCourse])
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByPlaceholder('e.g. CS375, CS445').fill('HUM101')
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await page.getByRole('button', { name: 'swap →', exact: true }).click()
  await expect(page.getByRole('button', { name: 'HUM201 Replacement', exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('requirement-picker.png'), fullPage: true })
  api.respond('POST', '/api/plan/generate', replacementPlan)
  await page.getByRole('button', { name: 'HUM201 Replacement', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(api.requests('POST', '/api/plan/generate')[1].postDataJSON().preferences).toMatchObject({
    courses: ['HUM201'], requirement_choices: choices,
  })
  await page.reload()
  await expect(page.getByText('Replacement', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Regenerate', exact: true })).toBeEnabled()
  expect(api.requests('POST', '/api/plan/generate').at(-1)!.postDataJSON().preferences.courses).toEqual(['HUM201'])
  await page.screenshot({ path: testInfo.outputPath('recalculated-preference.png'), fullPage: true })
})
