'use client'

import { useState, useEffect } from 'react'
import { getScraperStatus, isAbortError } from '@/lib/api'

const POLL_INTERVAL_MS = 3 * 60 * 1000  // 3 minutes
const STALE_THRESHOLD_MS = 45 * 60 * 1000  // 45 minutes

export function useScraperStatus() {
  const [lastScrape, setLastScrape] = useState<Date | null>(null)
  const [isStale, setIsStale] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    async function checkStatus() {
      try {
        const data = await getScraperStatus({ signal: controller.signal })
        if (controller.signal.aborted) return
        if (data.last_scrape) {
          const scrapeTime = new Date(data.last_scrape)
          setLastScrape(scrapeTime)
          setIsStale(Date.now() - scrapeTime.getTime() > STALE_THRESHOLD_MS)
        }
      } catch (error) {
        if (!isAbortError(error)) setIsStale(true)
      }
    }
    checkStatus()
    const interval = setInterval(checkStatus, POLL_INTERVAL_MS)
    return () => {
      controller.abort()
      clearInterval(interval)
    }
  }, [])

  return { lastScrape, isStale }
}
