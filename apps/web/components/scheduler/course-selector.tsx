'use client'

import { catalogCredits } from '@/lib/course-metadata'

import { useEffect, useRef, useState } from 'react'
import { Search, X } from 'lucide-react'
import { getCourses, getCoursesSections, getProfessor, type CourseResponse, type ProfessorResponse } from '@/lib/api'
import { useSchedulerStore } from '@/store/scheduler'
import { CourseCodePill } from '@/components/ui/course-code-pill'
import { ProfessorPicker } from './professor-picker'
import { CatalogCoverage } from './catalog-coverage'
import { CatalogNote } from '@/components/ui/catalog-note'

export function CourseSelector() {
  const { selectedCourses, term, addCourse, removeCourse, setProfessorCache, setProfessorsByCourse } =
    useSchedulerStore()

  const [query, setQuery] = useState('')
  const [results, setResults] = useState<CourseResponse[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [showDropdown, setShowDropdown] = useState(false)

  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  // Tracks which course+term combos have had a prefetch initiated this session
  const prefetchingRef = useRef(new Set<string>())

  // Debounced search — 300ms
  useEffect(() => {
    const trimmed = query.trim()
    if (!trimmed) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setResults([])
      setShowDropdown(false)
      return
    }
    setSearchLoading(true)
    const t = setTimeout(() => {
      getCourses({ q: trimmed, limit: 8 })
        .then((res) => {
          setResults(res)
          setShowDropdown(res.length > 0)
        })
        .catch(() => setResults([]))
        .finally(() => setSearchLoading(false))
    }, 300)
    return () => clearTimeout(t)
  }, [query])

  // Prefetch professor list + RMP for every selected course.
  // Fires on mount (picks up persisted courses) and whenever selectedCourses or term changes.
  // prefetchingRef prevents duplicate in-flight requests.
  useEffect(() => {
    for (const code of selectedCourses) {
      const key = `${code}:${term}`
      if (prefetchingRef.current.has(key)) continue
      prefetchingRef.current.add(key)
      getCoursesSections(code, term)
        .then((sections) => {
          const names = [
            ...new Set(sections.map((s) => s.professor_name).filter(Boolean)),
          ] as string[]
          setProfessorsByCourse(code, names)
          if (names.length === 0) return
          // Return the promise so a failed lookup reaches the catch below.
          // Only fulfilled lookups (including true 404s) may populate the cache.
          return Promise.all(
            names.map((n) =>
              getProfessor(n).then((data) => [n, data] as [string, ProfessorResponse | null]),
            ),
          ).then((entries) => setProfessorCache(Object.fromEntries(entries)))
        })
        .catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedCourses, term])

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
            placeholder="Search courses… (e.g. CS 280)"
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
                  <CourseCodePill code={course.course_code} />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm text-text truncate">{course.title || 'Title unavailable'}</span>
                    {course.title && course.title_status !== 'verified' && <span className="block text-xs text-faint">Title unverified</span>}
                    <span className="block text-xs text-muted font-mono">{catalogCredits(course)}</span>
                    <CatalogNote status={course.catalog_status} note={course.catalog_note} />
                    {course.metadata_warnings?.map((warning) => <span key={warning} className="block text-xs text-muted">{warning}</span>)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <CatalogCoverage term={term} query={query} />

      {/* Selected course cards */}
      {selectedCourses.length > 0 && (
        <ul className="flex flex-col gap-2">
          {selectedCourses.map((code) => (
            <li
              key={code}
              className="flex flex-col gap-1.5 px-3 py-2.5 rounded-lg bg-surface-2 border border-border"
            >
              <div className="flex items-center justify-between gap-2">
                <CourseCodePill code={code} />
                <button
                  onClick={() => removeCourse(code)}
                  className="text-faint hover:text-text transition-colors duration-150"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <ProfessorPicker courseCode={code} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
