import { test, expect } from './fixtures'
import { catalogCoverage, courses, planResponse, syntheticPdf } from './data'

test('keeps course search usable without the catalog coverage panel', async ({ page, api }, testInfo) => {
  api.respond('GET', '/api/catalog/coverage', {
    ...catalogCoverage,
    configured_subjects: ['CS', 'HSS'], elective_subjects: ['HUM'],
    subjects: [
      { subject: 'CS', configured: true, course_count: 1, section_count: 2 },
      { subject: 'HSS', configured: true, course_count: 0, section_count: 0 },
      { subject: 'HUM', configured: false, course_count: 0, section_count: 0 },
    ],
    warnings: ['No catalog courses collected for: HSS. The catalog is incomplete for these subjects.'],
  })
  await page.goto('/scheduler')
  await page.getByPlaceholder('Search courses… (e.g. CS 280)').fill('IS350')
  await expect.poll(() => api.requests('GET', '/api/courses').length).toBeGreaterThan(0)
  await expect(page.getByText('IS is outside the collected subject scope.', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Catalog coverage', { exact: true })).toHaveCount(0)
  await expect(page.getByText('No catalog courses collected for: HSS. The catalog is incomplete for these subjects.', { exact: true })).toHaveCount(0)
  await page.getByPlaceholder('Search courses… (e.g. CS 280)').fill('CS280')
  await page.getByRole('button', { name: 'CS280 Programming Language Concepts' }).click()
  await expect(page.getByText('CS280', { exact: true })).toBeVisible()
  expect(api.requests('GET', '/api/catalog/coverage')).toHaveLength(0)
  await page.screenshot({ path: testInfo.outputPath('course-picker.png'), fullPage: true })
})

test('keeps catalog warnings on required courses after reload', async ({ page, api }, testInfo) => {
  api.respond('POST', '/api/plan/generate', {
    ...planResponse,
    semesters: [{ ...planResponse.semesters[0], courses: [{
      ...planResponse.semesters[0].courses[0], catalog_status: 'subject_not_configured',
      catalog_note: 'Subject is outside the configured collection scope. Course data is not refreshed; confirm this course with NJIT.',
    }] }],
  })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  for (let attempt = 0; attempt < 2; attempt++) {
    await expect(page.getByText('Subject outside collection scope.', { exact: true })).toBeVisible()
    await expect(page.getByText('Subject is outside the configured collection scope. Course data is not refreshed; confirm this course with NJIT.', { exact: true })).toBeVisible()
    await expect(page.locator('span').getByText('Required', { exact: true })).toBeVisible()
    if (attempt === 0) await page.reload()
  }
  await page.screenshot({ path: testInfo.outputPath('plan-catalog-warning.png'), fullPage: true })
})

test('reports incomplete elective coverage and marks retained course rows', async ({ page, api }) => {
  api.respond('GET', '/api/plan/ger-courses', {
    subjects: ['HUM', 'HSS'], missing_subjects: ['HSS'], unconfigured_subjects: ['HUM'],
    warnings: ['Subjects outside collection scope: HUM. Data in these subjects is not refreshed.'],
    groups: [{ prefix: 'HUM', courses: [{ code: 'HUM201', title: 'Retained course', title_status: 'verified',
      catalog_status: 'subject_not_configured', catalog_note: 'This subject is not refreshed.',
    }] }],
  })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await page.getByRole('button', { name: 'swap →', exact: true }).click()
  await expect(page.getByText('Subjects outside collection scope: HUM. Data in these subjects is not refreshed.', { exact: true })).toBeVisible()
  await expect(page.getByText('Subject outside collection scope.', { exact: true })).toBeVisible()
  await expect(page.getByText('Browsing a subject does not confirm that a course satisfies this requirement.', { exact: true })).toBeVisible()
})

test('shows course results without collection scope or refresh warnings', async ({ page, api }) => {
  api.respond('GET', '/api/courses', [{ ...courses[0],
    catalog_status: 'subject_not_configured', catalog_note: 'This subject is not refreshed.',
    metadata_warnings: ['Title refresh: Conflicting section title.'],
  }])
  await page.goto('/scheduler')
  await page.getByPlaceholder('Search courses… (e.g. CS 280)').fill('CS')
  await expect(page.getByText('3 cr', { exact: true })).toBeVisible()
  await expect(page.getByText('Subject outside collection scope.', { exact: true })).toHaveCount(0)
  await expect(page.getByText('This subject is not refreshed.', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Title refresh: Conflicting section title.', { exact: true })).toHaveCount(0)
})
