'use client'

import { useState, useEffect, useRef } from 'react'
import { X, Search, ChevronDown, ChevronUp } from 'lucide-react'
import { getGerCourses, type GerGroup } from '@/lib/api'

interface GerModalProps {
  isOpen: boolean
  courseCode: string
  onClose: () => void
  onSwap: (newCode: string) => void
}

export function GerModal({ isOpen, courseCode, onClose, onSwap }: GerModalProps) {
  const [groups, setGroups] = useState<GerGroup[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!isOpen) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setQuery('')
    setLoading(true)
    setError(null)
    getGerCourses()
      .then((res) => {
        setGroups(res.groups)
        setExpanded(new Set(res.groups.map((g) => g.prefix)))
      })
      .catch(() => setError('Failed to load GER courses.'))
      .finally(() => setLoading(false))
    setTimeout(() => searchRef.current?.focus(), 50)
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
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

  function handleSwap(code: string) {
    onSwap(code)
    onClose()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl max-h-[80vh] flex flex-col rounded-xl border border-border bg-surface"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border flex-shrink-0">
          <div>
            <p className="text-xl font-semibold tracking-tight">GER Humanities Courses</p>
            <p className="text-xs text-muted mt-0.5">
              Swapping:{' '}
              <span className="font-mono text-text">{courseCode}</span>
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-md text-faint hover:text-text transition-colors duration-150"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Info + search */}
        <div className="px-6 pt-4 pb-3 flex-shrink-0">
          <p className="text-xs text-muted mb-3">
            Select a course to replace the current GER requirement slot.
          </p>
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
                          onClick={() => handleSwap(course.code)}
                          className="w-full flex items-baseline gap-3 px-6 py-1.5 hover:bg-surface-2 transition-colors duration-150 text-left"
                        >
                          <span className="font-mono text-xs text-text w-20 flex-shrink-0">
                            {course.code}
                          </span>
                          <span className="text-sm text-muted">{course.title || 'Title unavailable'}</span>
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
