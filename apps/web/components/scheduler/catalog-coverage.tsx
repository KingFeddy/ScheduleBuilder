'use client'

import { useEffect, useState } from 'react'
import { getCatalogCoverage, type CatalogCoverageResponse } from '@/lib/api'

export function CatalogCoverage({ term, query }: { term: string; query: string }) {
  const [coverage, setCoverage] = useState<CatalogCoverageResponse | null>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCoverage(null)
    setFailed(false)
    getCatalogCoverage(term, { signal: controller.signal })
      .then((response) => { if (!controller.signal.aborted) setCoverage(response) })
      .catch(() => { if (!controller.signal.aborted) setFailed(true) })
    return () => controller.abort()
  }, [term, attempt])

  if (failed) return (
    <div className="text-xs text-yellow flex items-center gap-2">
      <span>Catalog coverage unavailable.</span>
      <button onClick={() => setAttempt((value) => value + 1)} className="underline underline-offset-2">
        Retry catalog coverage
      </button>
    </div>
  )
  if (!coverage || coverage.term !== term) return <p className="text-xs text-muted">Checking catalog coverage…</p>

  const subject = /^([A-Z]{2,5})(?:\d|$)/.exec(query.toUpperCase().replace(/\s+/g, ''))?.[1]
  const outside = subject && !coverage.configured_subjects.includes(subject)
  const noCourses = subject && coverage.subjects.some((s) => s.subject === subject && s.configured && s.course_count === 0)

  return (
    <div className="text-xs text-muted flex flex-col gap-2">
      {outside && <p className="text-yellow"><span className="font-mono">{subject}</span> is outside the collected subject scope.</p>}
      {noCourses && <p className="text-yellow">No catalog courses collected for <span className="font-mono">{subject}</span>.</p>}
      <details className="rounded-lg border border-border bg-surface-2 px-3 py-2">
        <summary className="cursor-pointer">
          <span>Catalog coverage</span>
          {coverage.warnings.length > 0 && <span className="text-yellow"> — gaps detected</span>}
        </summary>
        <div className="flex flex-col gap-2 mt-2">
          <p>Course counts span the collected catalog. Section counts are for term <span className="font-mono">{term}</span>.</p>
          <p>Collected records do not confirm degree eligibility, freshness, or future availability.</p>
          {coverage.warnings.map((warning) => <p key={warning} className="text-yellow font-mono">{warning}</p>)}
          <div className="max-h-48 overflow-y-auto">
            <table className="w-full text-left font-mono">
              <thead><tr><th scope="col">Subject</th><th scope="col">Courses</th><th scope="col">Sections</th></tr></thead>
              <tbody>{coverage.subjects.map((s) => (
                <tr key={s.subject}><th scope="row" className="font-normal">{s.subject}{s.configured ? '' : ' (excluded)'}</th><td>{s.course_count}</td><td>{s.section_count}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      </details>
    </div>
  )
}
