import { test, expect } from './fixtures'

const success = {
  status: 'completed' as const, subjects: ['CS', 'HUM'], sections_upserted: 2, sections_failed: 0,
  started_at: '2026-09-13T12:00:00Z', finished_at: '2026-09-13T12:05:00Z', error_message: null,
}
const data = {
  term: '202690', status: 'completed' as const, checked_at: '2026-09-13T13:10:00Z',
  latest_attempt: success, last_successful_refresh: success, data_as_of: success.started_at,
  section_count: 2, sections_missing_timestamps: 0,
}

for (const status of ['partial', 'failed', 'running', 'skipped_overlap'] as const) {
  test(`shows ${status} separately from an older complete refresh`, async ({ page, api }, testInfo) => {
    api.respond('GET', '/api/scraper/status', { ...data, status,
      latest_attempt: { ...success, status, started_at: '2026-09-13T13:00:00Z', finished_at: data.checked_at },
    })
    await page.goto('/scheduler')
    await expect(page.getByText('Seat data may be 70+ min old.', { exact: false })).toBeVisible()
    await expect(page.getByText('Last full refresh: 65 min ago.', { exact: true })).toBeVisible()
    const labels = { partial: 'Partially completed', failed: 'Failed', running: 'Running', skipped_overlap: 'Skipped because another refresh was already running' }
    await expect(page.getByText(`Latest attempt: ${labels[status]}.`, { exact: true })).toBeVisible()
    if (status === 'partial') await page.screenshot({ path: testInfo.outputPath('partial-refresh.png'), fullPage: true })
  })
}

test('never-run and missing timestamps are visibly unknown instead of fresh', async ({ page, api }) => {
  api.respond('GET', '/api/scraper/status', { ...data, status: 'never_run', latest_attempt: null,
    last_successful_refresh: null, data_as_of: null,
  })
  await page.goto('/scheduler')
  await expect(page.getByText('Seat data age is unknown.', { exact: false })).toBeVisible()
  await expect(page.getByText('Latest attempt: Never run.', { exact: true })).toBeVisible()
  api.respond('GET', '/api/scraper/status', { ...data, data_as_of: null, sections_missing_timestamps: 1 })
  await page.reload()
  await expect(page.getByText('Seat data age is unknown.', { exact: false })).toBeVisible()
})

test('changing terms cancels old polling and clears the previous semester freshness', async ({ page, api }) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => { release = resolve })
  api.handle('GET', '/api/scraper/status', async (route) => {
    const term = new URL(route.request().url()).searchParams.get('term')
    if (term === '202690') {
      await pending
      await route.fulfill({ json: data })
    } else {
      await route.fulfill({ json: { ...data, term, status: 'never_run', latest_attempt: null,
        last_successful_refresh: null, data_as_of: null } })
    }
  })
  await page.goto('/scheduler')
  try {
    // Development Strict Mode may start and cancel an initial effect.
    await expect.poll(() => api.requests('GET', '/api/scraper/status').filter((r) => !r.failure()).length).toBe(1)
    const old = api.requests('GET', '/api/scraper/status').filter((r) => !r.failure())[0]
    await page.getByLabel('Semester', { exact: true }).selectOption('202710')
    await expect.poll(() => old.failure()?.errorText).toBe('net::ERR_ABORTED')
    await expect(page.getByText('Latest attempt: Never run.', { exact: true })).toBeVisible()
  } finally { release() }
  await expect(page.getByText('Last full refresh: 65 min ago.', { exact: true })).toHaveCount(0)
})

test('failed status checks show unknown age and allow retry', async ({ page, api }) => {
  api.respond('GET', '/api/scraper/status', { detail: 'Synthetic freshness outage' }, 503)
  await page.goto('/scheduler')
  await expect(page.getByText('Seat data age is unknown.', { exact: false })).toBeVisible()
  api.respond('GET', '/api/scraper/status', data)
  await page.getByRole('button', { name: 'Retry freshness', exact: true }).click()
  await expect(page.getByText('Last full refresh: 65 min ago.', { exact: true })).toBeVisible()
  expect(api.requests('GET', '/api/scraper/status').every((request) => new URL(request.url()).searchParams.get('term') === '202690')).toBe(true)
})

test('slow polling never overlaps and the displayed age continues advancing', async ({ page, api }) => {
  await page.clock.install({ time: new Date('2026-09-13T13:10:00Z') })
  api.respond('GET', '/api/scraper/status', data)
  await page.goto('/scheduler')
  await expect(page.getByText('Seat data may be 70+ min old.', { exact: false })).toBeVisible()
  const initial = api.requests('GET', '/api/scraper/status').length
  let release!: () => void
  const pending = new Promise<void>((resolve) => { release = resolve })
  api.handle('GET', '/api/scraper/status', async (route) => {
    await pending
    await route.fulfill({ json: data })
  })
  try {
    await page.clock.fastForward(3 * 60_000 + 100)
    await expect.poll(() => api.requests('GET', '/api/scraper/status').length).toBe(initial + 1)
    await page.clock.fastForward(3 * 60_000)
    await expect(page.getByText('Seat data may be 76+ min old.', { exact: false })).toBeVisible()
    expect(api.requests('GET', '/api/scraper/status')).toHaveLength(initial + 1)
    await page.getByRole('link', { name: 'Planner', exact: true }).click()
    await page.clock.fastForward(3 * 60_000)
    expect(api.requests('GET', '/api/scraper/status')).toHaveLength(initial + 1)
  } finally { release() }
})

test('a response for the wrong semester cannot supply its freshness', async ({ page, api }) => {
  api.respond('GET', '/api/scraper/status', { ...data, term: '202710' })
  await page.goto('/scheduler')
  await expect(page.getByRole('button', { name: 'Retry freshness', exact: true })).toBeVisible()
  await expect(page.getByText('Seat data age is unknown.', { exact: false })).toBeVisible()
  await expect(page.getByText('Last full refresh: 65 min ago.', { exact: true })).toHaveCount(0)
})
