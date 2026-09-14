import { createHash } from 'node:crypto'
import { test, expect } from './fixtures'
import { gerCoverage, parsedDegree, parseResponse, planResponse, presentCatalog, syntheticPdf } from './data'

test('uploads a synthetic audit, generates a plan, and restores it on reload', async ({ page, api }) => {
  await page.goto('/scheduler')
  await page.getByRole('link', { name: 'Planner', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Degree Planner', exact: true })).toBeVisible()
  await expect(page.getByText('Upload your DegreeWorks PDF', { exact: true })).toBeVisible()
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await expect(page.getByText('Synthetic Test Student', { exact: true })).toBeVisible()

  expect(api.requests('POST', '/api/plan/parse').map((r) => r.postDataJSON())).toEqual([{
    pdf_base64: syntheticPdf.buffer.toString('base64'),
    client_pdf_hash: createHash('sha256').update(syntheticPdf.buffer).digest('hex'),
  }])
  const elective = page.getByPlaceholder('e.g. CS375, CS445')
  await elective.fill('HUM 101')
  await elective.press('Enter')
  await page.getByRole('spinbutton', { name: 'Custom credits per semester' }).fill('3')
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate').map((r) => r.postDataJSON())).toEqual([{
    parsed_degree: parsedDegree,
    preferences: { courses: ['HUM101'], credits_per_semester: 3 },
  }])
  await expect(page.getByText('Programming Language Concepts', { exact: true })).toBeVisible()
  await expect(page.getByText('Writing and Communication', { exact: true })).toBeVisible()
  await expect(page.getByText('Fall 2026', { exact: true })).toBeVisible()
  await expect(page.getByText('Spring 2027', { exact: true })).toHaveCount(2)
  await expect(page.getByText('6 credits', { exact: true })).toBeVisible()
  await expect(page.getByText(planResponse.warnings[0], { exact: true })).toBeVisible()

  await page.reload()
  await expect(page.getByText('Loaded from your last session.', { exact: true })).toBeVisible()
  await expect(page.getByText('Synthetic Test Student', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  await expect(page.getByText('Programming Language Concepts', { exact: true })).toBeVisible()
  await expect(page.getByText('Writing and Communication', { exact: true })).toBeVisible()
  await expect(page.getByText(planResponse.warnings[0], { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/plan/parse')).toHaveLength(1)
  expect(api.requests('POST', '/api/plan/generate')).toHaveLength(1)
  // Preference hydration is tracked separately.
})

test('preserves replacement warnings through regeneration, swapping, and reload', async ({ page, api }) => {
  api.respond('GET', '/api/plan/ger-courses', { ...gerCoverage,
    groups: [{ prefix: 'HUM', courses: [{ ...presentCatalog, code: 'HUM201', title: 'Replacement', title_status: 'verified' }] }],
  })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByText(planResponse.warnings[0], { exact: true })).toBeVisible()
  const warnings = ['Synthetic unresolved requirement.', 'Synthetic unverified prerequisites.']
  api.respond('POST', '/api/plan/generate', { ...planResponse, warnings })
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
  await expect(page.getByText(warnings[0], { exact: true })).toBeVisible()
  await expect(page.getByText(planResponse.warnings[0], { exact: true })).toHaveCount(0)
  await page.reload()
  for (const warning of warnings) await expect(page.getByText(warning, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'swap →', exact: true }).click()
  await page.getByRole('button', { name: 'HUM201 Replacement', exact: true }).click()
  await page.reload()
  for (const warning of warnings) await expect(page.getByText(warning, { exact: true })).toBeVisible()
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-plan') || '{}').warnings)).toEqual(warnings)

  api.respond('POST', '/api/plan/generate', { ...planResponse, warnings: [] })
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
  await expect(page.getByText(warnings[0], { exact: true })).toHaveCount(0)
  await page.reload()
  for (const warning of warnings) await expect(page.getByText(warning, { exact: true })).toHaveCount(0)
  await expect(page.getByText('This saved plan does not include readable warnings. Regenerate it to check for unresolved requirements.', { exact: true })).toHaveCount(0)
})

for (const warnings of [undefined, 'invalid', ['Valid text', null]]) {
  test(`explains unavailable saved warnings (${JSON.stringify(warnings)}) without inventing a clean plan`, async ({ page }, testInfo) => {
    await page.addInitScript(({ parsed, plan }) => {
      try {
        localStorage.setItem('njit-dw-parsed', JSON.stringify(parsed))
        localStorage.setItem('njit-dw-plan', JSON.stringify(plan))
      } catch { /* Synthetic storage only. */ }
    }, { parsed: parsedDegree, plan: { semesters: planResponse.semesters, graduation: planResponse.projected_graduation, warnings } })
    await page.goto('/planner')
    await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
    await expect(page.getByText('This saved plan does not include readable warnings. Regenerate it to check for unresolved requirements.', { exact: true })).toBeVisible()
    if (warnings === undefined) await page.screenshot({ path: testInfo.outputPath('legacy-plan-warnings.png'), fullPage: true })
  })
}

test('rejects a non-PDF upload before calling the API', async ({ page, api }) => {
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles({
    name: 'synthetic-notes.txt', mimeType: 'text/plain', buffer: Buffer.from('Synthetic data only.'),
  })
  await expect(page.getByText('Please upload a PDF file.', { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/plan/parse')).toHaveLength(0)
})

test('shows a parse error and allows a successful retry', async ({ page, api }) => {
  api.respond('POST', '/api/plan/parse', { detail: 'Synthetic invalid audit' }, 422)
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await expect(page.getByText('Synthetic invalid audit', { exact: true })).toBeVisible()
  api.reset('POST', '/api/plan/parse')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await expect(page.getByText('Synthetic Test Student', { exact: true })).toBeVisible()
  expect(api.requests('POST', '/api/plan/parse')).toHaveLength(2)
})

test('shows a generation error and allows a successful retry', async ({ page, api }) => {
  api.respond('POST', '/api/plan/generate', { detail: 'Synthetic service unavailable' }, 503)
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByText('Failed to generate plan. Please try again.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Generate My Plan', exact: true })).toBeEnabled()
  api.reset('POST', '/api/plan/generate')
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  await expect(page.getByText('Failed to generate plan. Please try again.', { exact: true })).toHaveCount(0)
  expect(api.requests('POST', '/api/plan/generate')).toHaveLength(2)
})

test('shows missing degree metadata as unknown without inventing credit totals', async ({ page, api }) => {
  api.respond('POST', '/api/plan/parse', {
    ...parseResponse,
    parsed: {
      ...parsedDegree, student_name: null, catalog_year: null,
      credits_completed: null, credits_required: null, credits_remaining: null,
    },
  })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await expect(page.getByText('Student name unavailable', { exact: true })).toBeVisible()
  await expect(page.getByText('Unknown', { exact: true })).toHaveCount(3)
  await expect(page.getByText(/~.*semesters? remaining/)).toHaveCount(0)
  await expect(page.getByText('NaN', { exact: false })).toHaveCount(0)
})

test('searches GER courses when catalog titles are missing', async ({ page, api }) => {
  api.respond('GET', '/api/plan/ger-courses', {
    ...gerCoverage, groups: [{ prefix: 'HUM', courses: [{ ...presentCatalog, code: 'HUM101', title: null, title_status: 'missing' }] }],
  })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await page.getByRole('button', { name: 'swap →', exact: true }).click()
  await expect(page.getByText('Title unavailable', { exact: true })).toBeVisible()
  const search = page.getByPlaceholder('Search courses…', { exact: true })
  // A nonmatching code forces evaluation of the nullable title search branch.
  await search.fill('writing')
  await expect(page.getByText('No courses match your search.')).toBeVisible()
  await search.fill('hum101')
  await expect(page.getByRole('button', { name: 'HUM101 Title unavailable' })).toBeVisible()
})
