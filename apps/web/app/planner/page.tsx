'use client'

import { useState, useEffect, useRef } from 'react'
import { UploadZone } from '@/components/plan/upload-zone'
import { DegreeSummary } from '@/components/plan/degree-summary'
import { PreferencesForm } from '@/components/plan/preferences-form'
import { SemesterPlan } from '@/components/plan/semester-plan'
import { GerModal } from '@/components/plan/ger-modal'
import { replacementChoices, validChoices, type RequirementChoices } from '@/lib/planner-choices'
import type { PlannerPreferences } from '@/lib/planner-preferences'
import { usePlannerTerms } from '@/hooks/usePlannerTerms'
import { usePlannerPreferences } from '@/hooks/usePlannerPreferences'
import { encodeSavedAudit, restoreSavedAudit } from '@/lib/planner-audit'
import { encodeSavedPlan, restoreSavedPlan, isPlanForAudit, sameAudit, SAVED_PLAN_NOTICE, type PlanState } from '@/lib/planner-plan'
import {
  generatePlan,
  getApiErrorMessage,
  type ParsedDegreeValidated,
} from '@/lib/api'

function savePlan(plan: PlanState, audit: ParsedDegreeValidated) {
  try {
    localStorage.setItem('njit-dw-plan', encodeSavedPlan(plan, audit))
  } catch { /* Keep the current plan usable when browser storage is unavailable. */ }
}

export default function PlannerPage() {
  const plannerPreferences = usePlannerPreferences()
  const plannerTerms = usePlannerTerms()
  const startTerm = plannerPreferences.preferences?.startTerm || plannerTerms.defaultTerm
  const [parsed, setParsed] = useState<ParsedDegreeValidated | null>(null)
  const [loadedFromCache, setLoadedFromCache] = useState(false)
  const [auditNotice, setAuditNotice] = useState<string | null>(null)
  const [planNotice, setPlanNotice] = useState<string | null>(null)
  const [showUpload, setShowUpload] = useState(false)
  const [plan, setPlan] = useState<PlanState | null>(null)
  const [generating, setGenerating] = useState(false)
  const [gerModal, setGerModal] = useState<string | null>(null)
  const currentAudit = useRef<ParsedDegreeValidated | null>(null)

  const request = useRef<AbortController | null>(null)
  const modalCourse = plan?.semesters.flatMap((semester) => semester.courses).find((course) => course.slot_id === gerModal)

  useEffect(() => () => { currentAudit.current = null; request.current?.abort() }, [])

  function cancelGeneration() {
    request.current?.abort()
    request.current = null
    setGenerating(false)
  }

  function closeModal() {
    if (request.current) cancelGeneration()
    setGerModal(null)
    setPlanNotice(null)
  }

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
    cancelGeneration()
    setGerModal(null)
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

  async function runGeneration(
    preferences: PlannerPreferences,
    choices: RequirementChoices = plan?.requirementChoices || {},
    fallback = 'Failed to generate plan. Please try again.',
  ): Promise<boolean> {
    const submittedStartTerm = preferences.startTerm || plannerTerms.defaultTerm
    if (!parsed || !submittedStartTerm || request.current) return false
    const sourceAudit = parsed
    const controller = new AbortController()
    request.current = controller
    setGenerating(true)
    setPlanNotice(null)
    try {
      if (!validChoices(choices, sourceAudit)) {
        setPlanNotice('Your course choices no longer match this audit. Reset course choices and try again.')
        return false
      }
      const res = await generatePlan(sourceAudit, {
        courses: preferences.courses, credits_per_semester: preferences.creditsPerSemester,
        start_term: submittedStartTerm,
        ...(Object.keys(choices).length ? { requirement_choices: choices } : {}),
      }, { signal: controller.signal })
      if (request.current !== controller || controller.signal.aborted
        || !currentAudit.current || !sameAudit(currentAudit.current, sourceAudit)) return false
      const newPlan: PlanState = { semesters: res.semesters, graduation: res.projected_graduation,
        warnings: res.warnings, startTerm: submittedStartTerm,
        ...(Object.keys(choices).length ? { requirementChoices: choices } : {}) }
      if (!isPlanForAudit(newPlan, sourceAudit)) {
        setPlanNotice('The generated plan did not preserve your selected requirements. Your previous plan has been kept.')
        return false
      }
      setPlan(newPlan)
      savePlan(newPlan, sourceAudit)
      return true
    } catch (error) {
      if (request.current === controller && !controller.signal.aborted) setPlanNotice(getApiErrorMessage(error, fallback))
      return false
    } finally {
      if (request.current === controller) {
        request.current = null
        setGenerating(false)
      }
    }
  }

  async function handleRegenerate() {
    if (!plannerPreferences.preferences || !startTerm) return
    const preferences = { ...plannerPreferences.preferences, startTerm }
    plannerPreferences.update(preferences)
    await runGeneration(preferences, plan?.requirementChoices || {}, 'Could not regenerate the plan. Please try again.')
  }

  async function handleSwap(newCode: string) {
    if (!plan || !parsed || !modalCourse || !plannerPreferences.preferences || request.current) return
    try {
      const choices = replacementChoices(plan.semesters, plan.requirementChoices || {}, modalCourse.slot_id, newCode)
      const preferences = { ...plannerPreferences.preferences, startTerm,
        courses: [...new Set(plannerPreferences.preferences.courses.map((code) => code === modalCourse.course_code ? newCode : code))] }
      if (await runGeneration(preferences, choices, 'Could not replace this course. Your previous plan has been kept.')) {
        plannerPreferences.update(preferences)
        setGerModal(null)
      }
    } catch (error) {
      setPlanNotice(getApiErrorMessage(error, 'Could not replace this course. Your previous plan has been kept.'))
    }
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
              onClick={() => { cancelGeneration(); setShowUpload(true) }}
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
              generating={generating}
              preferences={plannerPreferences.preferences}
              defaultStartTerm={plannerTerms.defaultTerm}
              startTermError={plannerTerms.error}
              onRetryStartTerm={plannerTerms.retry}
              onPreferencesChange={plannerPreferences.update}
              onGenerate={runGeneration}
              onBrowseGer={() => setGerModal('browse')}
            /> : <p className="text-sm text-muted">Loading preferences…</p>}
          </div>

          {/* Right column */}
          <div className="min-w-0">
            {planNotice && !gerModal && <p role="status" className="text-sm text-muted mb-4">{planNotice}</p>}
            {!!plan?.requirementChoices && <button disabled={generating} onClick={() => {
              if (plannerPreferences.preferences) void runGeneration(plannerPreferences.preferences, {})
            }} className="mb-3 text-xs text-muted underline underline-offset-2 disabled:opacity-40">Reset course choices</button>}
            {plan ? (
              <SemesterPlan
                semesters={plan.semesters}
                graduation={plan.graduation}
                warnings={plan.warnings}
                generating={generating}
                regenerateDisabled={!plannerPreferences.preferences || !startTerm}
                startTerm={plan.startTerm}
                onRegenerate={handleRegenerate}
                onSwapCourse={!generating && plannerPreferences.preferences && startTerm ? (slotId) => {
                  setPlanNotice(null)
                  setGerModal(slotId)
                } : undefined}
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
        courseCode={modalCourse?.course_code || ''}
        requirement={modalCourse?.requirement || undefined}
        unavailable={[...parsed.completed_courses, ...parsed.in_progress_courses,
          ...(plan?.semesters.flatMap((semester) => semester.courses).filter((course) => course.slot_id !== gerModal)
            .map((course) => course.course_code) || [])]}
        submitting={generating}
        submitError={planNotice}
        onClose={closeModal}
        onSwap={modalCourse ? handleSwap : undefined}
      />
    </>
  )
}
