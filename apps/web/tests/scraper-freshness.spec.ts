import { test, expect } from '@playwright/test'
import { getFreshness } from '../lib/scraper-freshness'

const success = {
  status: 'completed' as const, subjects: ['CS'], sections_upserted: 2, sections_failed: 0,
  started_at: '2026-09-13T12:00:00Z', finished_at: '2026-09-13T12:05:00Z', error_message: null,
}
const data = {
  term: '202690', status: 'completed' as const, checked_at: '2026-09-13T12:10:00Z',
  latest_attempt: success, last_successful_refresh: success, data_as_of: success.started_at,
  section_count: 2, sections_missing_timestamps: 0,
}

test('ages from the server observation time and keeps aging without another response', () => {
  expect(getFreshness(data, 0)).toEqual({ freshness: 'fresh', ageMinutes: 10, lastCompleteMinutes: 5 })
  expect(getFreshness(data, 35 * 60_000).freshness).toBe('fresh')
  expect(getFreshness(data, 35 * 60_000 + 1).freshness).toBe('stale')
})

for (const status of ['partial', 'failed', 'running', 'skipped_overlap'] as const) {
  test(`${status} cannot reset the age of the last full refresh`, () => {
    const response = { ...data, status, latest_attempt: { ...success, status, finished_at: data.checked_at } }
    expect(getFreshness(response, 0).ageMinutes).toBe(10)
  })
}

for (const stamp of [null, '', 'invalid', '2026-09-13T13:00:00Z']) {
  test(`missing or invalid data time is unknown: ${stamp}`, () => {
    expect(getFreshness({ ...data, data_as_of: stamp }, 0).freshness).toBe('unknown')
  })
}

test('missing success, missing row timestamps, and an empty catalog are unknown', () => {
  expect(getFreshness({ ...data, last_successful_refresh: null }, 0).freshness).toBe('unknown')
  expect(getFreshness({ ...data, sections_missing_timestamps: 1 }, 0).freshness).toBe('unknown')
  expect(getFreshness({ ...data, section_count: 0 }, 0).freshness).toBe('unknown')
  expect(getFreshness({ ...data, checked_at: 'invalid' }, 0).freshness).toBe('unknown')
  expect(getFreshness({ ...data, last_successful_refresh: { ...success, finished_at: null } }, 0).freshness).toBe('unknown')
})
