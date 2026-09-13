'use client'

import { useEffect, useState } from 'react'
import { ApiError, getApiErrorMessage, getTerms, type TermsResponse } from '@/lib/api'
import { useSchedulerStore } from '@/store/scheduler'

export function useSchedulerTerms() {
  const [catalog, setCatalog] = useState<TermsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCatalog(null)
    setError(null)
    getTerms({ signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted) return
        if (!response.terms.some((option) => option.code === response.default_term)) {
          throw new ApiError('invalid-response', 'The server did not return its default semester. Please try again.')
        }
        const { preferredTerm, setTerm } = useSchedulerStore.getState()
        const saved = typeof preferredTerm === 'string' && response.terms.some((option) => option.code === preferredTerm)
          ? preferredTerm : null
        setTerm(saved || response.default_term, saved)
        setNotice(preferredTerm && !saved
          ? 'Your saved semester is no longer in the collected catalog. Using the default.' : null)
        setCatalog(response)
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(getApiErrorMessage(err, 'Could not load semesters. Please try again.'))
      })
    return () => controller.abort()
  }, [attempt])

  function selectTerm(preference: string) {
    if (!catalog) return
    const term = preference || catalog.default_term
    if (!catalog.terms.some((option) => option.code === term)) return
    useSchedulerStore.getState().setTerm(term, preference || null)
    setNotice(null)
  }

  return { catalog, error, notice, retry: () => setAttempt((value) => value + 1), selectTerm }
}
