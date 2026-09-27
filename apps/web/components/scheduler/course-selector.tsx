'use client'

import { catalogCredits, formatTopicTitle, formatCourseTitle } from '@/lib/course-metadata'

import { useEffect, useRef, useState } from 'react'
import { Search, X } from 'lucide-react'
import { getCourses, getCoursesSections, getProfessor, type CourseResponse, type ProfessorResponse } from '@/lib/api'
import { useSchedulerStore } from '@/store/scheduler'
import { ProfessorPicker } from './professor-picker'

function searchTitle(title: string | null): string {
  return /^(?:ST\s*[:–—-]|(?:special|selected)\s+topics\b)/i.test(title ?? '')
    ? 'Special Topics'
    : title || 'Title unavailable'
}

export function CourseSelector({ hasTermData }: { hasTermData: boolean }) {
  const { selectedCourses, term, termRevision, addCourse, removeCourse, setProfessorCache, setCourseSections, sectionsByCourse, topicPreferences, setTopicPreference } =
    useSchedulerStore()

  const [query, setQuery] = useState('')
  const [results, setResults] = useState<CourseResponse[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [showDropdown, setShowDropdown] = useState(false)

  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  // Tracks which course+term combos have had a prefetch initiated this session
  const prefetchingRef = useRef(new Set<string>())
  const termController = useRef<AbortController | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    termController.current = controller
    // Returning to a previously selected term must fetch its sections again.
    prefetchingRef.current = new Set()
    return () => controller.abort()
  }, [term, termRevision, hasTermData])

  // Debounced search — 300ms
  useEffect(() => {
    const trimmed = query.trim()
    if (!trimmed || !term || !hasTermData) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setResults([])
      setShowDropdown(false)
      setSearchLoading(false)
      return
    }
    const controller = new AbortController()
    setSearchLoading(true)
    const t = setTimeout(() => {
      getCourses({ q: trimmed, term, limit: 8 }, { signal: controller.signal })
        .then((res) => {
          if (controller.signal.aborted) return
          setResults(res)
          setShowDropdown(res.length > 0)
        })
        .catch(() => { if (!controller.signal.aborted) setResults([]) })
        .finally(() => { if (!controller.signal.aborted) setSearchLoading(false) })
    }, 300)
    return () => { clearTimeout(t); controller.abort() }
  }, [query, term, hasTermData])

  // Prefetch professor list + RMP for every selected course.
  // Fires on mount (picks up persisted courses) and whenever selectedCourses or term changes.
  // prefetchingRef prevents duplicate in-flight requests.
  useEffect(() => {
    const controller = termController.current
    if (!term || !hasTermData || !controller) return
    const prefetched = prefetchingRef.current
    const current = () => !controller.signal.aborted && useSchedulerStore.getState().termRevision === termRevision
    for (const code of selectedCourses) {
      const key = `${code}:${term}`
      if (prefetched.has(key)) continue
      prefetched.add(key)
      getCoursesSections(code, term, { signal: controller.signal })
        .then((sections) => {
          if (!current()) return
          const names = [
            ...new Set(sections.map((s) => s.professor_name).filter(Boolean)),
          ] as string[]
          setCourseSections(code, sections)
          if (names.length === 0) return
          // Return the promise so a failed lookup reaches the catch below.
          // Only fulfilled lookups (including true 404s) may populate the cache.
          return Promise.all(
            names.map((n) =>
              getProfessor(n, { signal: controller.signal }).then((data) => [n, data] as [string, ProfessorResponse | null]),
            ),
          ).then((entries) => { if (current()) setProfessorCache(Object.fromEntries(entries)) })
        })
        .catch(() => { prefetched.delete(key) })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedCourses, term, termRevision, hasTermData])

  // Close dropdown on outside click
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowDropdown(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])

  function selectCourse(code: string) {
    addCourse(code)
    setQuery('')
    setResults([])
    setShowDropdown(false)
    inputRef.current?.focus()
    // Prefetch is kicked off by the useEffect above when selectedCourses updates
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Search input */}
      <div ref={containerRef} className="relative">
        <div className="relative flex items-center">
          <Search className="absolute left-3 w-4 h-4 text-muted pointer-events-none" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onFocus={() => results.length > 0 && setShowDropdown(true)}
            placeholder="Search courses"
            className="w-full pl-9 pr-4 py-2 rounded-lg border border-border bg-surface-2 text-sm text-text placeholder:text-faint focus:outline-none focus:border-border-strong transition-colors duration-150"
          />
          {searchLoading && (
            <div className="absolute right-3 w-3 h-3 rounded-full bg-muted animate-pulse" />
          )}
        </div>

        {showDropdown && (
          <ul className="absolute z-50 w-full mt-1 rounded-lg border border-border bg-surface overflow-hidden">
            {results.map((course) => (
              <li key={course.course_code}>
                <button
                  onClick={() => selectCourse(course.course_code)}
                  className="w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-surface-2 transition-colors duration-150"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm text-text leading-snug break-words">
                      <span className="font-mono font-semibold">{course.course_code}</span>: {searchTitle(formatCourseTitle(course.title) ?? null)}
                    </span>
                    <span className="block text-xs text-muted font-mono">{catalogCredits(course)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Selected course cards */}
      {selectedCourses.length > 0 && (
        <ul className="flex flex-col gap-2">
          {selectedCourses.map((code) => {
            const sections = sectionsByCourse[code] ?? []
            const topics = [...new Set(sections.map((s) => s.topic).filter((t): t is string => !!t))]
            const selectedTopic = topics.includes(topicPreferences[code]) ? topicPreferences[code] : ''
            const title = formatCourseTitle(sections.find((s) => s.section_title)?.section_title)
            const hasTopics = hasTermData && topics.length > 0
            return (
            <li
              key={code}
              className="flex flex-col gap-1.5 px-3 py-2.5 rounded-lg bg-surface-2 border border-border"
            >
              <div className="flex items-start justify-between gap-2">
                {hasTopics ? (
                  <div className="min-w-0 flex-1 flex items-center gap-1 text-sm text-text">
                    <span className="shrink-0 font-mono font-semibold">{code}:</span>
                    <select
                      aria-label={`Topic for ${code}`}
                      title={selectedTopic ? formatTopicTitle(selectedTopic) : 'Choose a topic'}
                      value={selectedTopic}
                      onChange={(e) => setTopicPreference(code, e.target.value)}
                      className="min-w-0 flex-1 rounded-md border border-border bg-surface py-1 pl-1 text-sm text-text focus:outline-none focus:border-border-strong"
                    >
                      <option value="">Choose a topic…</option>
                      {topics.map((topic) => <option key={topic} value={topic}>{formatTopicTitle(topic)}</option>)}
                    </select>
                  </div>
                ) : (
                  <p className="min-w-0 text-sm text-text leading-snug break-words">
                    <span className="font-mono font-semibold">{code}</span>{title && <>: {title}</>}
                  </p>
                )}
                <button
                  aria-label={`Remove ${code}`}
                  onClick={() => removeCourse(code)}
                  className="shrink-0 mt-0.5 text-faint hover:text-text transition-colors duration-150"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              {hasTermData && (topics.length === 0 || selectedTopic) && <ProfessorPicker courseCode={code} />}
            </li>
          )})}
        </ul>
      )}
    </div>
  )
}
