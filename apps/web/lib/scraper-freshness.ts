import type { ScraperStatusResponse } from './api'

const STALE_THRESHOLD_MS = 45 * 60_000

export function getFreshness(data: ScraperStatusResponse, elapsedMs: number) {
  const checked = Date.parse(data.checked_at)
  const observed = data.data_as_of ? Date.parse(data.data_as_of) : NaN
  const success = data.last_successful_refresh
  const started = success?.started_at ? Date.parse(success.started_at) : NaN
  const finished = success?.finished_at ? Date.parse(success.finished_at) : NaN
  const now = checked + Math.max(0, elapsedMs)
  const validSuccess = success?.status === 'completed' && success.sections_failed === 0
    && Number.isFinite(started) && Number.isFinite(finished) && started <= finished && finished <= checked
  const lastCompleteMinutes = validSuccess && Number.isFinite(now) ? Math.floor((now - finished) / 60_000) : null
  if (!validSuccess || !Number.isFinite(now) || !Number.isFinite(observed) || observed > started
      || data.section_count <= 0 || data.sections_missing_timestamps !== 0) {
    return { freshness: 'unknown' as const, ageMinutes: null, lastCompleteMinutes }
  }
  return {
    freshness: now - observed > STALE_THRESHOLD_MS ? 'stale' as const : 'fresh' as const,
    ageMinutes: Math.floor((now - observed) / 60_000), lastCompleteMinutes,
  }
}
