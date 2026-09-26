'use client'

import { useEffect, useState } from 'react'
import { ApiError, getApiErrorMessage, getTerms, type TermsResponse } from '@/lib/api'
import { useSchedulerStore } from '@/store/scheduler'
import { SCHEDULER_TERM } from '@/lib/scheduler-term'

export function useSchedulerTerms() {
  const [catalog, setCatalog] = useState<TermsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCatalog(null)
    setError(null)
    getTerms({ signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted) return
        if (!response.terms.some((option) => option.code === SCHEDULER_TERM.code)) {
          throw new ApiError('invalid-response', `${SCHEDULER_TERM.label} is unavailable. Please try again later.`)
        }
        // Ignore saved selections and API default changes while the term is fixed.
        // setTerm also clears results and professor preferences from another term.
        useSchedulerStore.getState().setTerm(SCHEDULER_TERM.code, null)
        setCatalog(response)
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(getApiErrorMessage(err, 'Could not load semesters. Please try again.'))
      })
    return () => controller.abort()
  }, [attempt])

  return { catalog, error, retry: () => setAttempt((value) => value + 1) }
}
