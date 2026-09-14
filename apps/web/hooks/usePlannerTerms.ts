'use client'

import { useEffect, useState } from 'react'
import { getTerms, getApiErrorMessage } from '@/lib/api'
import { planningDefault } from '@/lib/planner-terms'

export function usePlannerTerms() {
  const [defaultTerm, setDefaultTerm] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    // Browser-only discovery; never invent a default from the current date.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setError(null)
    getTerms({ signal: controller.signal }).then((response) => {
      if (controller.signal.aborted) return
      const term = planningDefault(response.default_term)
      if (!term) throw new Error('Invalid planning default')
      setDefaultTerm(term)
    }).catch((err) => {
      if (!controller.signal.aborted) setError(getApiErrorMessage(err, 'Could not load the default start semester. Please retry.'))
    })
    return () => controller.abort()
  }, [attempt])
  return { defaultTerm, error, retry: () => setAttempt((value) => value + 1) }
}
