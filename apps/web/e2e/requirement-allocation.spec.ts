import { test, expect } from './fixtures'
import { replacementCourse, parsedDegree, parseResponse, planResponse, syntheticPdf } from './data'
import type { RequirementAllocation } from '../lib/api'

for (const unit of ['classes', 'credits'] as const) {
  test(`shows ${unit} allocation and its remainder after reload and regeneration`, async ({ page, api }, testInfo) => {
    const allocation: RequirementAllocation = {
      required_quantity: unit === 'classes' ? 2 : 6, quantity_unit: unit,
      allocated_quantity: unit === 'classes' ? 2 : 4,
      unresolved_quantity: unit === 'classes' ? 0 : 2,
      status: unit === 'classes' ? 'allocated' : 'partial',
    }
    const requirement = { ...parsedDegree.still_needed[0], requirement_id: 'req-multiple',
      remaining_quantity: allocation.required_quantity, quantity_unit: unit, options: ['CS280', 'HUM101'] }
    const semesters = planResponse.semesters.map((semester, i) => ({
      ...semester, total_credits: unit === 'classes' ? 3 : (i === 0 ? 4 : 2),
      courses: [{ ...semester.courses[0], slot_id: `slot-allocation-${i}`, requirement, allocation,
        credits: unit === 'classes' ? 3 : (i === 0 ? 4 : 2),
        ...(unit === 'credits' && i === 1 ? { course_code: 'TBD', title: 'Unresolved coursework',
          badge: 'TBD' as const, credits_estimated: true, title_status: 'unverified' as const,
          credits_note: 'Unresolved requirement credits; no qualifying course has been selected.',
          catalog_status: 'unresolved' as const, catalog_note: 'No specific catalog course has been selected for this slot.' } : {}),
      }],
    }))
    api.respond('POST', '/api/plan/parse', { ...parseResponse, parsed: { ...parsedDegree, still_needed: [requirement] } })
    api.respond('POST', '/api/plan/generate', { ...planResponse, semesters })
    await page.goto('/planner')
    await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
    await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
    const label = unit === 'classes' ? 'Allocated: 2 of 2 classes.' : 'Allocated: 4 of 6 credits.'
    await expect(page.getByText(label, { exact: true })).toHaveCount(2)
    if (unit === 'credits') await expect(page.getByText('Unresolved: 2 credits.', { exact: true })).toHaveCount(2)
    await page.reload()
    await expect(page.getByText(label, { exact: true })).toHaveCount(2)
    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-plan') || '{}').semesters)
    expect(stored.flatMap((s: { courses: { allocation: RequirementAllocation }[] }) => s.courses).map((c: { allocation: RequirementAllocation }) => c.allocation))
      .toEqual([allocation, allocation])
    await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
    await expect.poll(() => api.requests('POST', '/api/plan/generate').length).toBe(2)
    expect(api.requests('POST', '/api/plan/generate')[1].postDataJSON().parsed_degree.still_needed).toEqual([requirement])
    await expect(page.getByText(label, { exact: true })).toHaveCount(2)
    await expect(page.getByRole('button', { name: 'Regenerate', exact: true })).toBeEnabled()
    await page.screenshot({ path: testInfo.outputPath(`allocation-${unit}.png`), fullPage: true })
  })
}

test('a swap retains the other selected courses and uses regenerated allocation on every row', async ({ page, api }) => {
  const allocation: RequirementAllocation = { required_quantity: 2, quantity_unit: 'classes', allocated_quantity: 2,
    unresolved_quantity: 0, status: 'allocated' }
  const requirement = { ...parsedDegree.still_needed[1], remaining_quantity: 2, options: ['HUM101', 'HUM102', 'HUM103'] }
  const semesters = planResponse.semesters.map((semester, i) => ({ ...semester, courses: [{
    ...semester.courses[0], requirement, allocation, course_code: i === 0 ? 'HUM101' : 'HUM102',
  }] }))
  api.respond('POST', '/api/plan/generate', { ...planResponse, semesters })
  api.respond('POST', '/api/plan/parse', { ...parseResponse, parsed: { ...parsedDegree, still_needed: [requirement] } })
  api.respond('GET', '/api/courses', [{ ...replacementCourse, course_code: 'HUM103', title: 'Synthetic replacement' }])
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByText('Allocated: 2 of 2 classes.', { exact: true })).toHaveCount(2)
  await page.getByRole('button', { name: 'swap →', exact: true }).first().click()
  const updated = semesters.map((semester, i) => ({ ...semester,
    courses: semester.courses.map((course) => ({ ...course, course_code: i === 0 ? 'HUM103' : 'HUM102' })),
  }))
  api.respond('POST', '/api/plan/generate', { ...planResponse, semesters: updated })
  await page.getByRole('button', { name: 'HUM103 Synthetic replacement' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(api.requests('POST', '/api/plan/generate')[1].postDataJSON().preferences.requirement_choices)
    .toEqual({ 'req-writing': ['HUM103', 'HUM102'] })
  await expect(page.getByText('Allocated: 2 of 2 classes.', { exact: true })).toHaveCount(2)
  await page.reload()
  await expect(page.getByText('Allocated: 2 of 2 classes.', { exact: true })).toHaveCount(2)
})
