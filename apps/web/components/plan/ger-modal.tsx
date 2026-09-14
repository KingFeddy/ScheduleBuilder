'use client'

import { useState, useEffect, useRef } from 'react'
import { X, Search, ChevronDown, ChevronUp } from 'lucide-react'
import { getApiErrorMessage, getGerCourses, getCourses, type GerGroup, type StillNeededItem } from '@/lib/api'
import { matchesRequirement } from '@/lib/planner-choices'
import { CatalogNote } from '@/components/ui/catalog-note'

interface GerModalProps {
  isOpen: boolean
  courseCode: string
  onClose: () => void
  onSwap?: (newCode: string) => Promise<void>
  requirement?: StillNeededItem
  unavailable?: string[]
  submitting?: boolean
  submitError?: string | null
}

export function GerModal({ isOpen, courseCode, onClose, onSwap, requirement, unavailable = [], submitting = false, submitError }: GerModalProps) {
  const [groups, setGroups] = useState<GerGroup[]>([])
  const [coverageWarnings, setCoverageWarnings] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const searchRef = useRef<HTMLInputElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!isOpen) return
    const previouslyFocused = document.activeElement as HTMLElement | null
    const controller = new AbortController()
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setQuery('')
    setLoading(true)
    setError(null)
    setCoverageWarnings([])
    async function loadCourses() {
      if (!requirement) return getGerCourses({ signal: controller.signal })
      const subjects = requirement.options.includes('@') ? [undefined] : [...new Set(requirement.options
        .map((option) => /^([A-Z]{2,5})[0-9X]{3}[A-Z]?$/.exec(option)?.[1]).filter((subject): subject is string => !!subject))]
      const collected = new Map<string, GerGroup>()
      let requests = 0
      for (const subject of subjects) {
        for (let page = 1; ; page++) {
          if (++requests > 100) throw new Error('Course lookup limit reached')
          const courses = await getCourses({ subject, page, limit: 100 }, { signal: controller.signal })
          for (const course of courses.filter((item) => matchesRequirement(item.course_code, requirement!))) {
            const prefix = course.course_code.replace(/\d.*$/, '')
            const group = collected.get(prefix) || { prefix, courses: [] }
            if (!group.courses.some((item) => item.code === course.course_code)) group.courses.push({
              code: course.course_code, title: course.title, title_status: course.title_status,
              catalog_status: course.catalog_status, catalog_note: course.catalog_note,
            })
            collected.set(prefix, group)
          }
          if (courses.length < 100) break
        }
      }
      return { groups: [...collected.values()], warnings: [] }
    }
    loadCourses()
      .then((res) => {
        if (controller.signal.aborted) return
        setGroups(res.groups)
        setCoverageWarnings(res.warnings)
        setExpanded(new Set(res.groups.map((g) => g.prefix)))
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(getApiErrorMessage(err, requirement ? 'Could not load requirement options. Close and reopen to retry.' : 'Failed to load GER courses.'))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    const focusTimer = setTimeout(() => searchRef.current?.focus(), 50)
    return () => {
      controller.abort()
      clearTimeout(focusTimer)
      previouslyFocused?.focus()
    }
  }, [isOpen, requirement])

  useEffect(() => {
    if (!isOpen) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') { e.preventDefault(); onClose() }
      if (e.key === 'Tab') {
        const elements = [...(panelRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)') || [])]
        const first = elements[0], last = elements.at(-1)
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isOpen, onClose])

  if (!isOpen) return null

  const q = query.toLowerCase()
  const filteredGroups = groups
    .map((g) => ({
      ...g,
      courses: q
        ? g.courses.filter(
            (c) => c.code.toLowerCase().includes(q) || (c.title || '').toLowerCase().includes(q),
          )
        : g.courses,
    }))
    .filter((g) => g.courses.length > 0)

  function toggleGroup(prefix: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(prefix)) { next.delete(prefix) } else { next.add(prefix) }
      return next
    })
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        role="dialog" aria-modal="true" aria-label={requirement ? "Choose a replacement course" : "GER Humanities Courses"}
        className="w-full max-w-2xl max-h-[80vh] flex flex-col rounded-xl border border-border bg-surface"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border flex-shrink-0">
          <div>
            <p className="text-xl font-semibold tracking-tight">{requirement ? 'Choose a course' : 'GER Humanities Courses'}</p>
            {requirement && <p className="text-xs text-muted mt-0.5">
              {requirement.requirement} · Replacing:{' '}
              <span className="font-mono text-text">{courseCode}</span>
            </p>}
          </div>
          <button
            onClick={onClose}
            aria-label={submitting ? 'Cancel replacement' : 'Close course picker'}
            className="p-1.5 rounded-md text-faint hover:text-text transition-colors duration-150"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Info + search */}
        <div className="px-6 pt-4 pb-3 flex-shrink-0">
          <p className="text-xs text-muted mb-3">
            {requirement ? 'Choose a matching course to recalculate your plan. Completed and already planned courses cannot be selected again.' : 'Browse collected humanities courses, then add a course in your preferences.'}
          </p>
          <p className="text-xs text-muted mb-3">Browsing a subject does not confirm that a course satisfies this requirement.</p>
          {submitError && <p role="alert" className="text-sm text-njit-red mb-3">{submitError}</p>}
          {submitting && <p role="status" className="text-sm text-muted mb-3">Recalculating your plan… Close to cancel.</p>}
          <div className="relative flex items-center">
            <Search className="absolute left-3 w-4 h-4 text-muted pointer-events-none" />
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search courses…"
              className="w-full pl-9 pr-4 py-2 rounded-lg border border-border bg-surface-2 text-sm text-text placeholder:text-faint focus:outline-none focus:border-border-strong transition-colors duration-150"
            />
          </div>
        </div>

        {/* Scrollable list */}
        <div className="flex-1 overflow-y-auto">
          {!loading && !error && coverageWarnings.map((warning) => (
            <p key={warning} className="px-6 py-2 text-xs text-yellow font-mono">{warning}</p>
          ))}
          {loading ? (
            <div className="px-6 py-4 flex flex-col gap-5">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="flex flex-col gap-2">
                  <div className="h-3 w-20 rounded bg-surface-2 animate-pulse" />
                  {Array.from({ length: 3 }).map((_, j) => (
                    <div key={j} className="flex items-center gap-3 py-1">
                      <div className="h-3 w-16 rounded bg-surface-2 animate-pulse" />
                      <div className="flex-1 h-3 rounded bg-surface-2 animate-pulse" />
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ) : error ? (
            <p className="px-6 py-4 text-sm text-njit-red">{error}</p>
          ) : filteredGroups.length === 0 ? (
            <p className="px-6 py-4 text-sm text-muted">No courses match your search.</p>
          ) : (
            filteredGroups.map((group) => {
              const isExpanded = !!q || expanded.has(group.prefix)
              return (
                <div key={group.prefix}>
                  <button
                    onClick={() => toggleGroup(group.prefix)}
                    className="w-full flex items-center justify-between px-6 py-2.5 text-xs font-medium uppercase tracking-wider text-muted hover:bg-surface-2 transition-colors duration-150"
                  >
                    <span>{group.prefix}</span>
                    {isExpanded ? (
                      <ChevronUp className="w-3.5 h-3.5" />
                    ) : (
                      <ChevronDown className="w-3.5 h-3.5" />
                    )}
                  </button>

                  {isExpanded && (
                    <div>
                      {group.courses.map((course) => (
                        <button
                          key={course.code}
                          onClick={() => { if (onSwap && !submitting) void onSwap(course.code) }}
                          disabled={!onSwap || submitting || course.code === courseCode || unavailable.includes(course.code)}
                          className="w-full flex items-baseline gap-3 px-6 py-1.5 hover:bg-surface-2 transition-colors duration-150 text-left disabled:opacity-50"
                        >
                          <span className="font-mono text-xs text-text w-20 flex-shrink-0">
                            {course.code}
                          </span>
                          <span className="text-sm text-muted">
                            {course.title || 'Title unavailable'}
                            {course.title && course.title_status !== 'verified' && <span className="block text-xs text-faint">Title unverified</span>}
                            <CatalogNote status={course.catalog_status} note={course.catalog_note} />
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}
