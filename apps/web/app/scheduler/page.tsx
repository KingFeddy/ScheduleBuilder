'use client'

import { useMemo } from 'react'
import { Loader2, TriangleAlert } from 'lucide-react'
import { useSchedulerStore } from '@/store/scheduler'
import { solveSchedule, getProfessor, type ProfessorResponse } from '@/lib/api'
import { useScraperStatus } from '@/hooks/useScraperStatus'
import { CourseSelector } from '@/components/scheduler/course-selector'
import { CommuterToggles } from '@/components/scheduler/commuter-toggles'
import { ResultNavigator } from '@/components/scheduler/result-navigator'
import { ScheduleGrid } from '@/components/calendar/schedule-grid'

export default function SchedulerPage() {
  const {
    selectedCourses,
    term,
    commuterOptions,
    professorPreferences,
    results,
    activeResultIndex,
    isLoading,
    solveWarnings,
    setLoading,
    setResults,
    setError,
    setProfessorCache,
  } = useSchedulerStore()

  async function handleSolve() {
    if (isLoading || selectedCourses.length === 0) return
    setLoading(true)
    setError(null)
    try {
      const res = await solveSchedule({
        course_codes: selectedCourses,
        term,
        options: {
          earliest_start: commuterOptions.earliest_start || '07:00',
          latest_end: commuterOptions.latest_end || '22:00',
          minimize_gaps: commuterOptions.minimize_gaps,
        },
        compact_week: commuterOptions.compact_week,
        professor_preferences: Object.fromEntries(
          Object.entries(professorPreferences).filter(([, v]) => v.length > 0),
        ),
      })
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
            getProfessor(n).then((data) => [n, data] as [string, ProfessorResponse | null]),
          ),
        )
          .then((entries) => setProfessorCache(Object.fromEntries(entries)))
          .catch(() => {})
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to solve schedule')
    } finally {
      setLoading(false)
    }
  }

  const { lastScrape, isStale } = useScraperStatus()
  const activeResult = results[activeResultIndex] ?? null

  const staleBannerText = useMemo(() => {
    if (!lastScrape) return 'Seat availability data age is unknown — verify open seats in Banner before registering.'
    // eslint-disable-next-line react-hooks/purity
    const ageMin = Math.round((Date.now() - lastScrape.getTime()) / 60_000)
    return `Seat availability data is ${ageMin} min old — verify open seats in Banner before registering.`
  }, [lastScrape])

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Left panel */}
      <div className="w-72 flex-shrink-0 border-r border-border flex flex-col gap-6 p-5 overflow-y-auto">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-muted mb-3">
            Add Courses
          </p>
          <CourseSelector />
        </div>

        <div className="border-t border-border pt-5">
          <CommuterToggles />
        </div>

        <div className="border-t border-border pt-5 mt-auto flex flex-col gap-3">
          <button
            onClick={handleSolve}
            disabled={isLoading || selectedCourses.length === 0}
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

          {solveWarnings.length > 0 && (
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

      {/* Right panel */}
      <div className="flex-1 flex flex-col p-5 gap-4 min-w-0">
        {isStale && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-md border border-border bg-surface-2 text-xs text-yellow">
            <TriangleAlert className="w-3.5 h-3.5 flex-shrink-0" />
            <span>{staleBannerText}</span>
          </div>
        )}
        {results.length > 0 && <ResultNavigator />}
        <ScheduleGrid result={activeResult} />
      </div>
    </div>
  )
}
