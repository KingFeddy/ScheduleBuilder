'use client'

import { useEffect, useState } from 'react'
import { DEFAULT_PREFERENCES, PREFERENCES_NOTICE, restorePreferences, encodePreferences, type PlannerPreferences } from '@/lib/planner-preferences'

export function usePlannerPreferences() {
  const [preferences, setPreferences] = useState<PlannerPreferences | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    let restored = DEFAULT_PREFERENCES
    try {
      const raw = localStorage.getItem('njit-dw-preferences')
      if (raw !== null) {
        const saved = restorePreferences(raw)
        if (saved) {
          restored = saved
          // Upgrade valid records only; never write default state on mount.
          localStorage.setItem('njit-dw-preferences', encodePreferences(saved))
        } else {
          // Browser-only preferences are restored after hydration.
          // eslint-disable-next-line react-hooks/set-state-in-effect
          setNotice(PREFERENCES_NOTICE)
        }
      }
    } catch { /* In-memory preferences remain available without browser storage. */ }
    setPreferences(restored)
  }, [])

  function update(preferences: PlannerPreferences) {
    setPreferences(preferences)
    setNotice(null)
    try {
      localStorage.setItem('njit-dw-preferences', encodePreferences(preferences))
    } catch { /* Both generate actions use the same in-memory preferences. */ }
  }

  return { preferences, update, notice }
}
