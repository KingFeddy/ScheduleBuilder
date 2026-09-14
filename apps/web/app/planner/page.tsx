'use client'

import { useState, useEffect } from 'react'
import { UploadZone } from '@/components/plan/upload-zone'
import { DegreeSummary } from '@/components/plan/degree-summary'
import { PreferencesForm } from '@/components/plan/preferences-form'
import { SemesterPlan } from '@/components/plan/semester-plan'
import { GerModal } from '@/components/plan/ger-modal'
import {
  generatePlan,
  type ParsedDegreeValidated,
  type SemesterPlan as SemesterPlanType,
} from '@/lib/api'

interface PlanState {
  semesters: SemesterPlanType[]
  graduation: string
  warnings: string[]
}

function savePlan(plan: PlanState) {
  try {
    localStorage.setItem('njit-dw-plan', JSON.stringify(plan))
  } catch { /* Keep the current plan usable when browser storage is unavailable. */ }
}

interface GerModalState {
  semesterTerm: string
  courseCode: string
}

export default function PlannerPage() {
  const [parsed, setParsed] = useState<ParsedDegreeValidated | null>(null)
  const [loadedFromCache, setLoadedFromCache] = useState(false)
  const [showUpload, setShowUpload] = useState(false)
  const [plan, setPlan] = useState<PlanState | null>(null)
  const [generating, setGenerating] = useState(false)
  const [gerModal, setGerModal] = useState<GerModalState | null>(null)

  useEffect(() => {
    try {
      const raw = localStorage.getItem('njit-dw-parsed')
      if (raw) {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setParsed(JSON.parse(raw) as ParsedDegreeValidated)
        setLoadedFromCache(true)
      }
    } catch { /* ignore */ }

    try {
      const rawPlan = localStorage.getItem('njit-dw-plan')
      if (rawPlan) {
        const p = JSON.parse(rawPlan) as { semesters: SemesterPlanType[]; graduation: string; warnings?: unknown }
        // Older saves omitted warnings entirely. Their absence cannot establish
        // that the original generation had no unresolved requirements.
        const warnings = Array.isArray(p.warnings) && p.warnings.every((warning) => typeof warning === 'string')
          ? p.warnings
          : ['This saved plan does not include readable warnings. Regenerate it to check for unresolved requirements.']
        setPlan({ semesters: p.semesters, graduation: p.graduation, warnings })
      }
    } catch { /* ignore */ }
  }, [])

  function handleParsed(newParsed: ParsedDegreeValidated) {
    setParsed(newParsed)
    setLoadedFromCache(false)
    setShowUpload(false)
    setPlan(null)
    try { localStorage.removeItem('njit-dw-plan') } catch { /* ignore */ }
  }

  function handlePlanGenerated(
    semesters: SemesterPlanType[],
    graduation: string,
    warnings: string[],
  ) {
    const newPlan = { semesters, graduation, warnings }
    setPlan(newPlan)
    savePlan(newPlan)
  }

  async function handleRegenerate() {
    if (!parsed || generating) return
    setGenerating(true)
    try {
      let courses: string[] = []
      let credits_per_semester = 15
      try {
        const raw = localStorage.getItem('njit-dw-preferences')
        if (raw) {
          const prefs = JSON.parse(raw) as { courses: string[]; creditsPerSemester: number }
          courses = prefs.courses || []
          credits_per_semester = prefs.creditsPerSemester || 15
        }
      } catch { /* ignore */ }

      const res = await generatePlan(parsed, { courses, credits_per_semester })
      const newPlan: PlanState = {
        semesters: res.semesters,
        graduation: res.projected_graduation,
        warnings: res.warnings,
      }
      setPlan(newPlan)
      savePlan(newPlan)
    } catch { /* errors shown inline in SemesterPlan */ } finally {
      setGenerating(false)
    }
  }

  function handleSwap(newCode: string) {
    if (!plan || !gerModal?.courseCode) return
    const affectedRequirements = new Set(plan.semesters
      .filter((sem) => sem.term === gerModal.semesterTerm)
      .flatMap((sem) => sem.courses)
      .filter((course) => course.course_code === gerModal.courseCode && course.requirement)
      .map((course) => course.requirement!.requirement_id))
    const updated = plan.semesters.map((sem) => ({
      ...sem,
      courses: sem.courses.map((course) => {
        let updatedCourse = course
        if (sem.term === gerModal.semesterTerm && course.course_code === gerModal.courseCode) {
          updatedCourse = { ...course, course_code: newCode, title: null, title_status: 'missing',
            catalog_status: 'unknown', catalog_note: 'Catalog coverage for this replacement has not been checked. Regenerate the plan to check it.',
            credits_estimated: true, credits_note: 'Credits for this replacement are unverified; this amount is an estimate.' }
        }
        if (course.requirement && course.allocation && affectedRequirements.has(course.requirement.requirement_id)) {
          updatedCourse = { ...updatedCourse, allocation: { ...course.allocation,
            allocated_quantity: null, unresolved_quantity: null, status: 'unknown' } }
        }
        return updatedCourse
      }),
    }))
    const newPlan = { ...plan, semesters: updated }
    setPlan(newPlan)
    savePlan(newPlan)
  }

  // No degree data yet — full-page upload prompt
  if (!parsed || showUpload) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center p-10">
        <div className="w-full max-w-lg">
          <h1 className="text-xl font-semibold tracking-tight mb-1">Degree Planner</h1>
          <p className="text-sm text-muted mb-8">
            Upload your DegreeWorks PDF to generate a semester-by-semester graduation plan.
          </p>
          <UploadZone onParsed={handleParsed} />
          {showUpload && (
            <button
              onClick={() => setShowUpload(false)}
              className="mt-4 text-xs text-muted underline underline-offset-2 hover:text-text transition-colors duration-150"
            >
              ← Back to my plan
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="mx-auto max-w-6xl px-6 py-10">
        <h1 className="text-xl font-semibold tracking-tight">Degree Planner</h1>
        <p className="text-sm text-muted mt-1">
          Review your progress and generate a semester-by-semester graduation plan.
        </p>

        {loadedFromCache && (
          <div className="mt-6 flex items-center justify-between rounded-lg border border-border bg-surface-2 px-4 py-2.5">
            <p className="text-xs text-muted">Loaded from your last session.</p>
            <button
              onClick={() => setShowUpload(true)}
              className="text-xs text-muted underline underline-offset-2 hover:text-text transition-colors duration-150"
            >
              Upload new PDF
            </button>
          </div>
        )}

        <div className="mt-8 grid grid-cols-1 lg:grid-cols-[340px_1fr] gap-8">
          {/* Left column */}
          <div className="space-y-6">
            <DegreeSummary parsed={parsed} />
            <PreferencesForm
              parsed={parsed}
              onPlanGenerated={handlePlanGenerated}
              onBrowseGer={() => setGerModal({ semesterTerm: '', courseCode: '' })}
            />
          </div>

          {/* Right column */}
          <div className="min-w-0">
            {plan ? (
              <SemesterPlan
                semesters={plan.semesters}
                graduation={plan.graduation}
                warnings={plan.warnings}
                generating={generating}
                onRegenerate={handleRegenerate}
                onSwapCourse={(semesterTerm, courseCode) =>
                  setGerModal({ semesterTerm, courseCode })
                }
              />
            ) : (
              <div className="flex items-center justify-center h-64 rounded-xl border border-border bg-surface">
                <p className="text-sm text-muted">
                  Generate a plan using the form on the left.
                </p>
              </div>
            )}
          </div>
        </div>
      </div>

      <GerModal
        isOpen={gerModal !== null}
        courseCode={gerModal?.courseCode ?? ''}
        onClose={() => setGerModal(null)}
        onSwap={handleSwap}
      />
    </>
  )
}
