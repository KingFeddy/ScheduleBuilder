'use client'

import { useEffect, useRef } from 'react'
import { Loader2 } from 'lucide-react'
import { useSchedulerStore } from '@/store/scheduler'
import { solveSchedule, getApiErrorMessage, getProfessor, isAbortError, type ProfessorResponse } from '@/lib/api'
import { CourseSelector } from '@/components/scheduler/course-selector'
import { CommuterToggles } from '@/components/scheduler/commuter-toggles'
import { ResultNavigator } from '@/components/scheduler/result-navigator'
import { ScheduleGrid } from '@/components/calendar/schedule-grid'
import { TermStatus } from '@/components/scheduler/term-status'
import { useSchedulerTerms } from '@/hooks/useSchedulerTerms'

export default function SchedulerPage() {
  const {
    selectedCourses,
    term,
    termRevision,
    commuterOptions,
    professorPreferences,
    topicPreferences,
    sectionsByCourse,
    results,
    activeResultIndex,
    isLoading,
    solveWarnings,
    setLoading,
    setResults,
    setError,
    setProfessorCache,
  } = useSchedulerStore()
  const terms = useSchedulerTerms()
  const selectedTerm = terms.catalog?.terms.find((option) => option.code === term)
  const hasTermData = selectedTerm?.has_data === true
  const needsTopic = selectedCourses.some((code) => {
    const sections = sectionsByCourse[code] ?? []
    return sections.some((s) => s.topic) && !sections.some((s) => s.topic === topicPreferences[code])
  })
  const solveRequest = useRef<AbortController | null>(null)

  useEffect(() => () => {
    solveRequest.current?.abort()
    // An unmounted page still owns the shared loading flag for this revision.
    // A semester change already resets it and may start a newer request.
    if (useSchedulerStore.getState().termRevision === termRevision) setLoading(false)
  }, [termRevision, setLoading])

  async function handleSolve() {
    if (isLoading || selectedCourses.length === 0 || !hasTermData || needsTopic) return
    const controller = new AbortController()
    solveRequest.current = controller
    const revision = termRevision
    const stillCurrent = () => !controller.signal.aborted && useSchedulerStore.getState().termRevision === revision
    setLoading(true)
    setError(null)
    try {
      const res = await solveSchedule({
        course_codes: selectedCourses,
        term,
        options: {
          earliest_start: commuterOptions.earliest_start || '07:00',
          latest_end: commuterOptions.latest_end || '22:30',
          minimize_gaps: true,
          hide_full_sections: commuterOptions.hide_full_sections,
        },
        compact_week: commuterOptions.compact_week,
        topic_preferences: Object.fromEntries(
          Object.entries(topicPreferences).filter(([code, topic]) => selectedCourses.includes(code) && topic),
        ),
        professor_preferences: Object.fromEntries(
          Object.entries(professorPreferences).filter(([, v]) => v.length > 0),
        ),
      }, { signal: controller.signal })
      if (!stillCurrent()) return
      setResults(res.results, res.warnings)

      // Prefetch RMP data for every professor in the results so the modal
      // opens instantly instead of waiting for an on-demand fetch.
      const names = [
        ...new Set(
          res.results
            .flatMap((r) => r.sections.map((s) => s.professor_name))
            .filter(Boolean),
        ),
      ] as string[]
      if (names.length > 0) {
        Promise.all(
          names.map((n) =>
            getProfessor(n, { signal: controller.signal }).then((data) => [n, data] as [string, ProfessorResponse | null]),
          ),
        )
          .then((entries) => { if (stillCurrent()) setProfessorCache(Object.fromEntries(entries)) })
          .catch(() => {})
      }
    } catch (err) {
      if (stillCurrent() && !isAbortError(err)) setError(getApiErrorMessage(err, 'Failed to solve schedule. Please try again.'))
    } finally {
      if (stillCurrent()) setLoading(false)
    }
  }

  const activeResult = hasTermData ? results[activeResultIndex] ?? null : null

  return (
    <div className="flex min-h-0 flex-1 gap-5 p-5 overflow-hidden">
      {/* Left panel */}
      <div className="w-80 min-h-0 flex-shrink-0 rounded-xl border border-border flex flex-col gap-5 p-5 overflow-y-auto">
        <div className="min-h-48 flex-1 overflow-y-auto overscroll-contain">
          <TermStatus catalog={terms.catalog} error={terms.error} onRetry={terms.retry} />
          <p className="text-xs font-medium uppercase tracking-wider text-muted mb-3">
            Add Courses
          </p>
          <CourseSelector hasTermData={hasTermData} />
        </div>

        <div className="shrink-0 border-t border-border pt-5 flex flex-col gap-5">
          <CommuterToggles />

          <div className="border-t border-border pt-5 flex flex-col gap-3">
            <button
              onClick={handleSolve}
              disabled={isLoading || selectedCourses.length === 0 || !hasTermData || needsTopic}
              className="w-full flex items-center justify-center gap-2 py-2 rounded-lg text-sm font-medium bg-njit-red text-white hover:opacity-90 disabled:opacity-40 transition-opacity duration-150"
            >
              {isLoading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Solving…
                </>
              ) : (
                'Solve'
              )}
            </button>

            {hasTermData && solveWarnings.length > 0 && (
              <ul className="flex flex-col gap-1.5">
                {solveWarnings.map((w, i) => (
                  <li
                    key={i}
                    className="text-xs text-yellow px-2 py-1 rounded bg-surface-2 border border-border"
                  >
                    {w}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>

      {/* Right panel */}
      <div className="flex-1 flex flex-col gap-4 min-w-0 min-h-0">
        {hasTermData && results.length > 0 && <ResultNavigator />}
        <ScheduleGrid result={activeResult} />
      </div>
    </div>
  )
}
