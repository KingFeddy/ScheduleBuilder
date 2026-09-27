'use client'

import type { TermsResponse } from '@/lib/api'
import { SCHEDULER_TERM } from '@/lib/scheduler-term'

interface TermStatusProps {
  catalog: TermsResponse | null
  error: string | null
  onRetry: () => void
}

export function TermStatus({ catalog, error, onRetry }: TermStatusProps) {
  const current = catalog?.terms.find((option) => option.code === SCHEDULER_TERM.code)
  if (!error && (!current || current.has_data)) return null

  return (
    <div className="flex flex-col gap-2">
      {error ? (
        <div className="text-xs text-yellow flex flex-col gap-2">
          <p>Semester information is unavailable.</p>
          <p>{error}</p>
          <button onClick={onRetry} className="text-left underline underline-offset-2">Retry</button>
        </div>
      ) : current && !current.has_data ? (
        <p className="text-xs text-yellow">No section data has been collected for {SCHEDULER_TERM.label}. Please try again later.</p>
      ) : null}
    </div>
  )
}
