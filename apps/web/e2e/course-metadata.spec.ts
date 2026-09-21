import { test, expect } from './fixtures'
import { replacementCourse, replacementPlan, parsedDegree, planResponse, presentCatalog, syntheticPdf } from './data'

test('shows credit metadata without title warnings in course search', async ({ page, api }, testInfo) => {
  const metadata = { ...presentCatalog, title_status: 'verified' as const, credits_min: null, credits_max: null, credits_options: [], metadata_warnings: [] }
  api.respond('GET', '/api/courses', [
    { ...metadata, course_code: 'ZZZ101', title: 'One credit', credits: 1, credits_status: 'fixed', credits_min: 1, credits_max: 1 },
    { ...metadata, course_code: 'ZZZ104', title: 'Four credits', credits: 4, credits_status: 'fixed', credits_min: 4, credits_max: 4 },
    { ...metadata, course_code: 'ZZZ199', title: null, title_status: 'missing', credits: null, credits_status: 'missing' },
    { ...metadata, course_code: 'ZZZ200', title: 'Research', credits: null, credits_status: 'variable', credits_min: 1, credits_max: 4 },
    { ...metadata, course_code: 'ZZZ201', title: 'Legacy', title_status: 'unverified', credits: 3, credits_status: 'unverified' },
    { ...metadata, course_code: 'ZZZ202', title: 'Discrete credits', credits: null, credits_status: 'variable', credits_min: 1, credits_max: 4, credits_options: [1, 4] },
  ])
  await page.goto('/scheduler')
  await page.getByPlaceholder('Search courses… (e.g. CS 280)').fill('ZZZ')
  await expect(page.getByText('1 cr', { exact: true })).toBeVisible()
  await expect(page.getByText('4 cr', { exact: true })).toBeVisible()
  await expect(page.getByText('Credits unknown', { exact: true })).toBeVisible()
  await expect(page.getByText('1–4 cr (variable)', { exact: true })).toBeVisible()
  await expect(page.getByText('3 cr (unverified)', { exact: true })).toBeVisible()
  await expect(page.getByText('Title unverified', { exact: true })).toHaveCount(0)
  await expect(page.getByText('1 or 4 cr (variable)', { exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('catalog-metadata.png'), fullPage: true })
})

test('shows estimates on required course rows and totals after generation and reload', async ({ page, api }, testInfo) => {
  api.respond('POST', '/api/plan/generate', {
    ...planResponse,
    semesters: [{ term: '202690', term_label: 'Fall 2026', total_credits: 8, courses: [
      { ...presentCatalog, slot_id: 'slot-metadata-1', requirement: null, allocation: null, course_code: 'ZZZ101', title: 'Verified lab', title_status: 'verified', credits: 1, credits_estimated: false, credits_note: '', badge: 'Required', reason: '' },
      { ...presentCatalog, slot_id: 'slot-metadata-2', requirement: null, allocation: null, course_code: 'ZZZ104', title: 'Verified class', title_status: 'verified', credits: 4, credits_estimated: false, credits_note: '', badge: 'Required', reason: '' },
      { ...presentCatalog, slot_id: 'slot-metadata-3', requirement: null, allocation: null, course_code: 'ZZZ199', title: 'Requirement label', title_status: 'unverified', credits: 3, credits_estimated: true, credits_note: 'Credits unknown; using 3 credits as an estimate.', badge: 'Required', reason: '' },
    ] }],
  })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  for (let attempt = 0; attempt < 2; attempt++) {
    await expect(page.getByText('1 cr', { exact: true })).toBeVisible()
    await expect(page.getByText('4 cr', { exact: true })).toBeVisible()
    await expect(page.getByText('3 cr (estimated)', { exact: true })).toBeVisible()
    await expect(page.getByText('8 credits (estimated)', { exact: true })).toHaveCount(2)
    await expect(page.getByText('Credits unknown; using 3 credits as an estimate.', { exact: true })).toBeVisible()
    if (attempt === 0) await page.reload()
  }
  await page.screenshot({ path: testInfo.outputPath('planned-credit-estimates.png'), fullPage: true })
})

test('requires regeneration for an older unbound saved plan', async ({ page }) => {
  await page.addInitScript(({ degree, plan }) => {
    try {
      localStorage.setItem('njit-dw-parsed', JSON.stringify(degree))
      localStorage.setItem('njit-dw-plan', JSON.stringify(plan))
    } catch (error) { throw new Error('Could not seed synthetic test storage', { cause: error }) }
  }, { degree: parsedDegree, plan: { graduation: 'Fall 2026', semesters: [{ term: '202690', term_label: 'Fall 2026', total_credits: 3,
    courses: [{ course_code: 'ZZZ199', title: 'Legacy', credits: 3, badge: 'Required', reason: '' }],
  }] } })
  await page.goto('/planner')
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toHaveCount(0)
  await expect(page.getByText('Your saved plan is outdated, damaged, or belongs to a different audit. Generate a new plan to continue.', { exact: true })).toBeVisible()
})

test('uses recalculated credits and metadata for a replacement', async ({ page, api }) => {
  api.respond('GET', '/api/courses', [{ ...replacementCourse, title_status: 'unverified' }])
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await page.getByRole('button', { name: 'swap →', exact: true }).click()
  await expect(page.getByText('Title unverified', { exact: true })).toBeVisible()
  api.respond('POST', '/api/plan/generate', replacementPlan)
  await page.getByRole('button', { name: 'HUM201 Replacement Title unverified' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByText('4 cr', { exact: true })).toBeVisible()
  await expect(page.getByText('Title unverified', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Catalog coverage unchecked.', { exact: true })).toHaveCount(0)
})
