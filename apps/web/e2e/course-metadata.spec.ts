import { test, expect } from './fixtures'
import { planResponse, presentCatalog, syntheticPdf } from './data'

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
