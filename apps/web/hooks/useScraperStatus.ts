'use client'

import { useState, useEffect } from 'react'
import { ApiError, getScraperStatus, type ScraperStatusResponse } from '@/lib/api'
import { getFreshness } from '@/lib/scraper-freshness'

const POLL_INTERVAL_MS = 3 * 60 * 1000  // 3 minutes

export function useScraperStatus(term: string) {
  const [attempt, setAttempt] = useState(0)
  const key = `${term}:${attempt}`
  const [snapshot, setSnapshot] = useState<{
    key: string; data: ScraperStatusResponse | null; error: boolean; requestedAt: number
  } | null>(null)
  const [now, setNow] = useState(0)

  useEffect(() => {
    if (!term) return
    const controller = new AbortController()
    let poll: ReturnType<typeof setTimeout> | undefined
    async function checkStatus() {
      const requestedAt = performance.now()
      try {
        const data = await getScraperStatus(term, { signal: controller.signal })
        if (controller.signal.aborted) return
        if (data.term !== term) {
          throw new ApiError('invalid-response', 'The server returned freshness for a different semester.')
        }
        setSnapshot({ key, data, error: false, requestedAt })
        setNow(performance.now())
      } catch {
        if (!controller.signal.aborted) setSnapshot({ key, data: null, error: true, requestedAt })
      } finally {
        // Schedule after completion so slow requests can never overlap and race.
        if (!controller.signal.aborted) poll = setTimeout(checkStatus, POLL_INTERVAL_MS)
      }
    }
    checkStatus()
    // Age continues advancing even when the API is slow or unavailable. Use
    // elapsed monotonic time, not the student's possibly incorrect system clock.
    const ageTimer = setInterval(() => setNow(performance.now()), 30_000)
    return () => {
      controller.abort()
      clearTimeout(poll)
      clearInterval(ageTimer)
    }
  }, [term, key])

  const current = snapshot?.key === key ? snapshot : null
  return {
    data: current?.data || null, error: current?.error || false, loading: !current,
    freshness: current?.data ? getFreshness(current.data, now - current.requestedAt) : null,
    retry: () => setAttempt((value) => value + 1),
  }
}
