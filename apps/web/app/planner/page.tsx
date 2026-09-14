'use client'

import { useState, useEffect, useRef } from 'react'
import { UploadZone } from '@/components/plan/upload-zone'
import { DegreeSummary } from '@/components/plan/degree-summary'
import { PreferencesForm } from '@/components/plan/preferences-form'
import { SemesterPlan } from '@/components/plan/semester-plan'
import { GerModal } from '@/components/plan/ger-modal'
import { usePlannerPreferences } from '@/hooks/usePlannerPreferences'
import { encodeSavedAudit, restoreSavedAudit } from '@/lib/planner-audit'
import { encodeSavedPlan, restoreSavedPlan, isPlanForAudit, sameAudit, SAVED_PLAN_NOTICE, type PlanState } from '@/lib/planner-plan'
import {
  generatePlan,
  getApiErrorMessage,
  type ParsedDegreeValidated,
  type SemesterPlan as SemesterPlanType,
} from '@/lib/api'

function savePlan(plan: PlanState, audit: ParsedDegreeValidated) {
  try {
    localStorage.setItem('njit-dw-plan', encodeSavedPlan(plan, audit))
  } catch { /* Keep the current plan usable when browser storage is unavailable. */ }
}

interface GerModalState {
  semesterTerm: string
  courseCode: string
}

export default function PlannerPage() {
  const plannerPreferences = usePlannerPreferences()
  const [parsed, setParsed] = useState<ParsedDegreeValidated | null>(null)
  const [loadedFromCache, setLoadedFromCache] = useState(false)
  const [auditNotice, setAuditNotice] = useState<string | null>(null)
  const [planNotice, setPlanNotice] = useState<string | null>(null)
  const [showUpload, setShowUpload] = useState(false)
  const [plan, setPlan] = useState<PlanState | null>(null)
  const [generating, setGenerating] = useState(false)
  const [gerModal, setGerModal] = useState<GerModalState | null>(null)
  const currentAudit = useRef<ParsedDegreeValidated | null>(null)

  useEffect(() => () => { currentAudit.current = null }, [])

  useEffect(() => {
    let restored: ParsedDegreeValidated | null = null
    try {
      const raw = localStorage.getItem('njit-dw-parsed')
      if (raw) {
        restored = restoreSavedAudit(raw)
        if (!restored) {
          // Browser storage is restored after hydration, never during SSR.
          // eslint-disable-next-line react-hooks/set-state-in-effect
          setAuditNotice('Your saved audit is incomplete or uses an unsupported format. Upload your DegreeWorks PDF again to refresh the requirements.')
          return
        }
        setParsed(restored)
        currentAudit.current = restored
        setLoadedFromCache(true)
        localStorage.setItem('njit-dw-parsed', encodeSavedAudit(restored))
      }
    } catch { /* ignore */ }

    // A generated plan cannot be restored without a usable source audit.
    if (!restored) return
    try {
      const rawPlan = localStorage.getItem('njit-dw-plan')
      if (rawPlan) {
        const saved = restoreSavedPlan(rawPlan, restored)
        if (saved) setPlan(saved)
        else setPlanNotice(SAVED_PLAN_NOTICE)
      }
    } catch { /* ignore */ }
  }, [])

  function handleParsed(newParsed: ParsedDegreeValidated) {
    currentAudit.current = newParsed
    setParsed(newParsed)
    setAuditNotice(null)
    setPlanNotice(null)
    setGenerating(false)
    setLoadedFromCache(false)
    setShowUpload(false)
    setPlan(null)
    try { localStorage.removeItem('njit-dw-plan') } catch { /* ignore */ }
  }

  function handlePlanGenerated(
    semesters: SemesterPlanType[],
    graduation: string,
    warnings: string[],
    sourceAudit: ParsedDegreeValidated,
  ) {
    if (!currentAudit.current || !sameAudit(currentAudit.current, sourceAudit)) return
    const newPlan = { semesters, graduation, warnings }
    if (!isPlanForAudit(newPlan, sourceAudit)) throw new Error('Invalid generated plan')
    setPlan(newPlan)
    setPlanNotice(null)
    savePlan(newPlan, sourceAudit)
  }

  async function handleRegenerate() {
    if (!parsed || !plannerPreferences.preferences || generating) return
    const sourceAudit = parsed
    setGenerating(true)
    setPlanNotice(null)
    try {
      const { courses, creditsPerSemester } = plannerPreferences.preferences
      const res = await generatePlan(parsed, { courses, credits_per_semester: creditsPerSemester })
      handlePlanGenerated(res.semesters, res.projected_graduation, res.warnings, sourceAudit)
    } catch (error) {
      if (currentAudit.current && sameAudit(currentAudit.current, sourceAudit)) {
        setPlanNotice(getApiErrorMessage(error, 'Could not regenerate the plan. Please try again.'))
      }
    } finally {
      if (currentAudit.current && sameAudit(currentAudit.current, sourceAudit)) setGenerating(false)
    }
  }

  function handleSwap(newCode: string) {
    if (!plan || !parsed || !gerModal?.courseCode) return
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
    savePlan(newPlan, parsed)
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
          {auditNotice && <p role="status" className="text-sm text-muted mb-6">{auditNotice}</p>}
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
            {plannerPreferences.notice && <p role="status" className="text-sm text-muted">{plannerPreferences.notice}</p>}
            {plannerPreferences.preferences ? <PreferencesForm
              parsed={parsed}
              preferences={plannerPreferences.preferences}
              onPreferencesChange={plannerPreferences.update}
              onPlanGenerated={handlePlanGenerated}
              onBrowseGer={() => setGerModal({ semesterTerm: '', courseCode: '' })}
            /> : <p className="text-sm text-muted">Loading preferences…</p>}
          </div>

          {/* Right column */}
          <div className="min-w-0">
            {planNotice && <p role="status" className="text-sm text-muted mb-4">{planNotice}</p>}
            {plan ? (
              <SemesterPlan
                semesters={plan.semesters}
                graduation={plan.graduation}
                warnings={plan.warnings}
                generating={generating || !plannerPreferences.preferences}
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
