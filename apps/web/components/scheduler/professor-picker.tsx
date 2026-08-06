'use client'

import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Check, Star, Flame } from 'lucide-react'
import { useSchedulerStore } from '@/store/scheduler'
import { ProfessorModal, ratingColor, difficultyColor } from './professor-modal'

// ─── Professor name overflow cell ────────────────────────────────────────────

function ProfessorNameCell({
  name,
  onOpenModal,
}: {
  name: string
  onOpenModal: () => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const textRef = useRef<HTMLSpanElement>(null)
  const [overflow, setOverflow] = useState(0)
  const [hovered, setHovered] = useState(false)

  useEffect(() => {
    if (containerRef.current && textRef.current) {
      setOverflow(
        Math.max(0, textRef.current.scrollWidth - containerRef.current.clientWidth),
      )
    }
  }, [name])

  const display = name.includes(',')
    ? (() => {
        const [last, first = ''] = name.split(', ')
        return `${first} ${last}`.trim()
      })()
    : name

  return (
    <div
      ref={containerRef}
      className="flex-1 min-w-0 overflow-hidden cursor-pointer"
      style={{
        maskImage: 'linear-gradient(to right, black 70%, transparent 100%)',
        WebkitMaskImage: 'linear-gradient(to right, black 70%, transparent 100%)',
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={onOpenModal}
    >
      <span
        ref={textRef}
        className="text-xs text-text whitespace-nowrap inline-block transition-transform duration-[2000ms]"
        style={{
          transform:
            hovered && overflow > 0 ? `translateX(-${overflow}px)` : 'translateX(0)',
        }}
      >
        {display}
      </span>
    </div>
  )
}

// ─── ProfessorPicker ─────────────────────────────────────────────────────────

interface ProfessorPickerProps {
  courseCode: string
}

export function ProfessorPicker({ courseCode }: ProfessorPickerProps) {
  const {
    professorPreferences,
    setProfessorPreferences,
    professorsByCourse,
    professorCache,
  } = useSchedulerStore()

  const selected = professorPreferences[courseCode] ?? []
  // undefined = prefetch not yet complete; [] = completed but no professors found
  const professors = professorsByCourse[courseCode]

  const [open, setOpen] = useState(false)
  const [activeModal, setActiveModal] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  function toggle(name: string) {
    const next = selected.includes(name)
      ? selected.filter((n) => n !== name)
      : [...selected, name]
    setProfessorPreferences(courseCode, next)
  }

  function openModal(name: string) {
    setOpen(false)
    setActiveModal(name)
  }

  // Trigger label
  let triggerLabel: string
  if (selected.length === 0) {
    triggerLabel = 'Any professor'
  } else if (selected.length === 1) {
    const raw = selected[0]
    const last = raw.includes(',') ? raw.split(', ')[0] : raw
    triggerLabel = `Prof. ${last}`
  } else {
    triggerLabel = `${selected.length} professors`
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 text-xs text-text hover:text-muted transition-colors duration-150"
      >
        <ChevronDown className="w-3 h-3 flex-shrink-0" />
        <span>{triggerLabel}</span>
      </button>

      {open && (
        <div className="absolute z-50 w-64 top-full mt-1 rounded-lg border border-border bg-surface overflow-hidden">
          {professors === undefined ? (
            <div className="px-3 py-2.5 text-xs text-muted">Loading professors…</div>
          ) : professors.length === 0 ? (
            <div className="px-3 py-2.5 text-xs text-muted">No sections found</div>
          ) : (
            <div className="overflow-y-auto max-h-52">
              {professors.map((name) => {
                const checked = selected.includes(name)
                const r = professorCache[name]
                const rc = r?.rmp_score != null ? ratingColor(r.rmp_score) : 'text-faint'
                const dc =
                  r?.rmp_difficulty != null ? difficultyColor(r.rmp_difficulty) : 'text-faint'
                return (
                  <div
                    key={name}
                    className="flex items-center gap-2 px-3 py-2 hover:bg-surface-2 transition-colors duration-150"
                  >
                    {/* Checkbox */}
                    <button
                      onClick={() => toggle(name)}
                      className="flex-shrink-0 flex items-center justify-center w-3.5 h-3.5 rounded-sm border transition-colors duration-150"
                      style={{
                        background: checked ? 'var(--njit-red)' : 'transparent',
                        borderColor: checked ? 'var(--njit-red)' : 'var(--border-strong)',
                      }}
                    >
                      {checked && <Check className="w-2.5 h-2.5 text-white" strokeWidth={3} />}
                    </button>

                    {/* Name with overflow fade + slow hover scroll */}
                    <ProfessorNameCell name={name} onOpenModal={() => openModal(name)} />

                    {/* Ratings — read from store cache, already populated by CourseSelector prefetch */}
                    <div
                      className="flex items-center gap-1.5 flex-shrink-0"
                      style={{ width: '6rem' }}
                    >
                      <span className={`inline-flex items-center gap-0.5 text-xs font-mono ${rc}`}>
                        <Star className="w-2.5 h-2.5" />
                        {r?.rmp_score != null ? r.rmp_score.toFixed(1) : 'N/A'}
                      </span>
                      <span className={`inline-flex items-center gap-0.5 text-xs font-mono ${dc}`}>
                        <Flame className="w-2.5 h-2.5" />
                        {r?.rmp_difficulty != null ? r.rmp_difficulty.toFixed(1) : 'N/A'}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {activeModal && (
        <ProfessorModal
          professorName={activeModal}
          onClose={() => setActiveModal(null)}
        />
      )}
    </div>
  )
}
