'use client'

import type { TermsResponse } from '@/lib/api'

interface TermSelectorProps {
  catalog: TermsResponse | null
  term: string
  preference: string | null
  error: string | null
  notice: string | null
  onRetry: () => void
  onChange: (preference: string) => void
}

export function TermSelector({ catalog, term, preference, error, notice, onRetry, onChange }: TermSelectorProps) {
  if (error) return (
    <div className="text-xs text-yellow flex flex-col gap-2">
      <p>Semester information is unavailable.</p>
      <p>{error}</p>
      <button onClick={onRetry} className="text-left underline underline-offset-2">Retry semesters</button>
    </div>
  )
  if (!catalog) return <p className="text-xs text-muted" role="status">Loading semesters…</p>

  const defaultTerm = catalog.terms.find((option) => option.code === catalog.default_term)!
  const selected = catalog.terms.find((option) => option.code === term)
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor="schedule-semester" className="text-xs font-medium uppercase tracking-wider text-muted">Semester</label>
      <select id="schedule-semester" value={preference || ''} onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-border bg-surface-2 px-2 py-2 text-sm font-mono text-text focus:outline-none focus:border-border-strong">
        <option value="">Default: {defaultTerm.label}{defaultTerm.has_data ? '' : ' (no data)'}</option>
        {catalog.terms.map((option) => <option key={option.code} value={option.code}>
          {option.label}{option.has_data ? '' : ' (no data)'}
        </option>)}
      </select>
      <p className="text-xs text-muted">Keep the default to follow future semester updates.</p>
      {notice && <p className="text-xs text-yellow">{notice}</p>}
      {selected && !selected.has_data && <div className="text-xs text-yellow">
        <p>No section data has been collected for <span className="font-mono">{selected.label}</span>.</p>
        <p>Choose another semester with data or try again later. This does not mean no classes are offered.</p>
      </div>}
    </div>
  )
}
