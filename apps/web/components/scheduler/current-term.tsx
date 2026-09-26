'use client'

import type { TermsResponse } from '@/lib/api'
import { SCHEDULER_TERM } from '@/lib/scheduler-term'

interface CurrentTermProps {
  catalog: TermsResponse | null
  error: string | null
  onRetry: () => void
}

export function CurrentTerm({ catalog, error, onRetry }: CurrentTermProps) {
  const current = catalog?.terms.find((option) => option.code === SCHEDULER_TERM.code)

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-medium uppercase tracking-wider text-muted">Current semester</p>
      <p className="text-sm font-mono text-text">{SCHEDULER_TERM.label}</p>
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
