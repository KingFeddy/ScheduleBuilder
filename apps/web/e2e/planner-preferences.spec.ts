import { test, expect } from './fixtures'
import { parsedDegree, syntheticPdf } from './data'

test('restores preferences before saving and uses current settings for both generate actions', async ({ page, api }) => {
  await page.addInitScript((audit) => {
    try {
      if (!localStorage.getItem('njit-dw-parsed')) {
        localStorage.setItem('njit-dw-parsed', JSON.stringify(audit))
        localStorage.setItem('njit-dw-preferences', JSON.stringify({ courses: ['cs 435'], creditsPerSemester: 12 }))
      }
    } catch { /* Synthetic browser storage. */ }
  }, parsedDegree)
  await page.goto('/planner')
  const credits = page.getByRole('spinbutton', { name: 'Custom credits per semester' })
  await expect(credits).toHaveValue('12')
  await expect(page.getByText('CS435', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-preferences') || '{}')))
    .toEqual({ version: 1, courses: ['CS435'], creditsPerSemester: 12 })
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate')[0].postDataJSON().preferences).toEqual({ courses: ['CS435'], credits_per_semester: 12 })
  await page.reload()
  await expect(credits).toHaveValue('12')
  await credits.fill('9')
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
  await expect.poll(() => api.requests('POST', '/api/plan/generate').length).toBe(2)
  expect(api.requests('POST', '/api/plan/generate')[1].postDataJSON().preferences).toEqual({ courses: ['CS435'], credits_per_semester: 9 })
  await page.getByText('CS435', { exact: true }).getByRole('button').click()
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
  await expect.poll(() => api.requests('POST', '/api/plan/generate').length).toBe(3)
  expect(api.requests('POST', '/api/plan/generate')[2].postDataJSON().preferences).toEqual({ courses: [], credits_per_semester: 9 })
})

test('uses in-memory preferences when browser storage is unavailable', async ({ page, api }) => {
  await page.addInitScript(() => {
    for (const method of ['getItem', 'setItem', 'removeItem'] as const) {
      const original = Storage.prototype[method]
      Object.defineProperty(Storage.prototype, method, { configurable: true, value: function (this: Storage, key: string, value?: string) {
        if (key === 'njit-dw-preferences') throw new DOMException('Synthetic storage restriction', 'SecurityError')
        return Reflect.apply(original, this, value === undefined ? [key] : [key, value])
      } })
    }
  })
  await page.goto('/planner')
  await page.locator('input[type="file"]').setInputFiles(syntheticPdf)
  await page.getByPlaceholder('e.g. CS375, CS445').fill('CS435')
  const credits = page.getByRole('spinbutton', { name: 'Custom credits per semester' })
  await credits.fill('9')
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate')[0].postDataJSON().preferences).toEqual({ courses: ['CS435'], credits_per_semester: 9 })
  await credits.fill('6')
  await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
  await expect.poll(() => api.requests('POST', '/api/plan/generate').length).toBe(2)
  expect(api.requests('POST', '/api/plan/generate')[1].postDataJSON().preferences).toEqual({ courses: ['CS435'], credits_per_semester: 6 })
})

test('recovers from malformed preferences without overwriting them during restoration', async ({ page, api }, testInfo) => {
  await page.addInitScript((audit) => {
    try {
      localStorage.setItem('njit-dw-parsed', JSON.stringify(audit))
      localStorage.setItem('njit-dw-preferences', JSON.stringify({ courses: null, creditsPerSemester: '12' }))
    } catch { /* Synthetic browser storage. */ }
  }, parsedDegree)
  await page.goto('/planner')
  await expect(page.getByText('Your saved preferences could not be restored. Review your course choices and credit target before generating a plan.', { exact: true })).toBeVisible()
  await expect(page.getByRole('spinbutton', { name: 'Custom credits per semester' })).toHaveValue('15')
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('njit-dw-preferences') || '{}'))).toEqual({ courses: null, creditsPerSemester: '12' })
  await page.screenshot({ path: testInfo.outputPath('preferences-recovery.png'), fullPage: true })
  await page.getByRole('button', { name: 'Generate My Plan', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your Academic Plan' })).toBeVisible()
  expect(api.requests('POST', '/api/plan/generate')[0].postDataJSON().preferences).toEqual({ courses: [], credits_per_semester: 15 })
})
