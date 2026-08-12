'use client'

import { Printer, RefreshCw } from 'lucide-react'
import type { SemesterPlan as SemesterPlanType } from '@/lib/api'

// Known GER subject prefixes at NJIT — swap button shown only for these
const GER_PREFIXES = new Set(['HUM', 'COM', 'HIST', 'STS', 'LIB', 'SSC'])

function isGerCourse(code: string): boolean {
  const prefix = code.replace(/\d.*$/, '')
  return GER_PREFIXES.has(prefix)
}

interface CourseRowProps {
  code: string
  title: string | null
  credits: number
  badge: 'Required' | 'Elective' | 'TBD'
  reason: string
  onSwap?: () => void
}

function CourseRow({ code, title, credits, badge, reason, onSwap }: CourseRowProps) {
  const showSwap = (badge === 'Required' || badge === 'Elective') && isGerCourse(code) && !!onSwap
  const showReason = badge !== 'Required' && !!reason

  return (
    <div className="flex items-center gap-3 px-5 py-3 hover:bg-surface-2 transition-colors duration-150 group">
      <span className="font-mono text-sm text-text w-20 flex-shrink-0">{code}</span>
      <span className="flex-1 min-w-0">
        <span className="block text-sm text-muted group-hover:text-text transition-colors duration-150 truncate">
          {title ?? code}
        </span>
        {showReason && (
          <span className="block text-xs text-faint truncate">{reason}</span>
        )}
      </span>
      {showSwap && (
        <button
          onClick={onSwap}
          className="text-xs text-muted underline underline-offset-2 hover:text-text flex-shrink-0 transition-colors duration-150"
        >
          swap →
        </button>
      )}
      <span className="font-mono tabular-nums text-xs text-faint w-12 text-right flex-shrink-0">
        {credits} cr
      </span>
      <span
        className={[
          'text-xs font-mono px-2 py-0.5 rounded-md border flex-shrink-0',
          badge === 'Required'
            ? 'bg-red-dim border-njit-red text-njit-red'
            : 'bg-surface-2 border-border text-faint',
        ].join(' ')}
      >
        {badge}
      </span>
    </div>
  )
}

function SkeletonCard() {
  return (
    <div className="rounded-xl border border-border bg-surface overflow-hidden">
      <div className="h-10 bg-surface-2 animate-pulse" />
      <div className="divide-y divide-border">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3 px-5 py-3">
            <div className="h-3 w-16 rounded bg-surface-2 animate-pulse" />
            <div className="flex-1 h-3 rounded bg-surface-2 animate-pulse" />
            <div className="h-3 w-10 rounded bg-surface-2 animate-pulse" />
            <div className="h-5 w-16 rounded-md bg-surface-2 animate-pulse" />
          </div>
        ))}
      </div>
    </div>
  )
}

interface SemesterPlanProps {
  semesters: SemesterPlanType[]
  graduation: string
  warnings: string[]
  generating?: boolean
  onRegenerate: () => void
  onSwapCourse?: (semesterTerm: string, courseCode: string) => void
}

export function SemesterPlan({
  semesters,
  graduation,
  warnings,
  generating = false,
  onRegenerate,
  onSwapCourse,
}: SemesterPlanProps) {
  const totalCredits = semesters.reduce((sum, s) => sum + s.total_credits, 0)

  return (
    <div className="flex flex-col gap-4">
      {/* Header row */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Your Academic Plan</h2>
          <p className="text-sm text-muted mt-0.5">
            Projected graduation:{' '}
            <span className="font-mono text-text">{graduation}</span>
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            onClick={onRegenerate}
            disabled={generating}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border bg-surface-2 text-sm text-muted hover:text-text hover:border-border-strong disabled:opacity-40 transition-colors duration-150"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Regenerate
          </button>
          <button
            onClick={() => window.print()}
            disabled={generating}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border bg-surface-2 text-sm text-muted hover:text-text hover:border-border-strong disabled:opacity-40 transition-colors duration-150"
          >
            <Printer className="w-3.5 h-3.5" />
            Export PDF
          </button>
        </div>
      </div>

      {/* Plan warnings */}
      {warnings.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {warnings.map((w, i) => (
            <li
              key={i}
              className="text-xs text-yellow px-3 py-1.5 rounded-lg bg-surface-2 border border-border"
            >
              {w}
            </li>
          ))}
        </ul>
      )}

      {generating ? (
        <>
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </>
      ) : (
        <>
          {semesters.map((semester) => (
            <div
              key={semester.term}
              className="rounded-xl border border-border bg-surface overflow-hidden"
            >
              <div className="flex items-center justify-between px-5 py-3 border-b border-border bg-surface-2">
                <span className="font-mono text-sm font-bold text-text">
                  {semester.term_label}
                </span>
                <span className="font-mono tabular-nums text-xs text-muted">
                  {semester.total_credits} credits
                </span>
              </div>
              <div className="divide-y divide-border">
                {semester.courses.map((course) => (
                  <CourseRow
                    key={course.course_code}
                    code={course.course_code}
                    title={course.title}
                    credits={course.credits}
                    badge={course.badge}
                    reason={course.reason}
                    onSwap={
                      onSwapCourse
                        ? () => onSwapCourse(semester.term, course.course_code)
                        : undefined
                    }
                  />
                ))}
              </div>
            </div>
          ))}

          {/* Total summary row */}
          <div className="rounded-xl border border-border bg-surface overflow-hidden">
            <div className="flex items-center justify-between px-5 py-3 bg-surface-2">
              <span className="font-mono text-sm font-bold text-text">
                Total credits planned
              </span>
              <span className="font-mono tabular-nums text-xs text-muted">
                {totalCredits} credits
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
