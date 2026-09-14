'use client'

import { useScraperStatus } from '@/hooks/useScraperStatus'
import type { ScraperStatusResponse } from '@/lib/api'

const labels: Record<ScraperStatusResponse['status'], string> = {
  completed: 'Completed', partial: 'Partially completed', failed: 'Failed',
  running: 'Running', never_run: 'Never run', blocked: 'Blocked by Banner',
  schema_change: 'Stopped because Banner changed',
  skipped_overlap: 'Skipped because another refresh was already running',
}

export function ScraperFreshness({ term }: { term: string }) {
  const { data, freshness, loading, error, retry } = useScraperStatus(term)
  const caution = error || freshness?.freshness !== 'fresh' || data?.status !== 'completed'
  return (
    <div role="status" aria-label="Seat data freshness"
      className={`flex flex-col gap-1 px-3 py-2 rounded-md border border-border bg-surface-2 text-xs ${loading ? 'text-muted' : caution ? 'text-yellow' : 'text-muted'}`}>
      {loading ? <p>Checking seat data freshness…</p> : <>
        <p>{freshness?.ageMinutes == null ? 'Seat data age is unknown.' : <>
          Seat data may be <span className="font-mono">{freshness.ageMinutes}+</span> min old.
        </>} Verify open seats in Banner before registering.</p>
        {error ? <>
          <p>Could not check the latest refresh.</p>
          <button onClick={retry} className="self-start underline underline-offset-2">Retry freshness</button>
        </> : <>
          <p>Latest attempt: {data ? labels[data.status] || 'Unknown' : 'Unknown'}.</p>
          <p>{freshness?.lastCompleteMinutes == null ? 'Last full refresh: unknown.' : <>
            Last full refresh: <span className="font-mono">{freshness.lastCompleteMinutes}</span> min ago.
          </>}</p>
        </>}
      </>}
    </div>
  )
}
