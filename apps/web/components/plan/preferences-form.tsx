'use client'

import { useState, useRef, type KeyboardEvent } from 'react'
import { X, Loader2 } from 'lucide-react'
import { generatePlan, getApiErrorMessage, type ParsedDegreeValidated, type SemesterPlan } from '@/lib/api'
import { planningTermLabel, planningTermOptions } from '@/lib/planner-terms'
import { normalizeElective, type PlannerPreferences } from '@/lib/planner-preferences'

interface PreferencesFormProps {
  parsed: ParsedDegreeValidated
  preferences: PlannerPreferences
  defaultStartTerm: string | null
  startTermError: string | null
  onRetryStartTerm: () => void
  onPreferencesChange: (preferences: PlannerPreferences) => void
  onPlanGenerated: (semesters: SemesterPlan[], graduation: string, warnings: string[], sourceAudit: ParsedDegreeValidated, startTerm: string) => void
  onBrowseGer?: () => void
}

const PRESET_OPTIONS = [
  { label: 'Light', credits: 12 },
  { label: 'Normal', credits: 15 },
  { label: 'Heavy', credits: 17 },
] as const

const MIN_CUSTOM_CREDITS = 3
const MAX_CUSTOM_CREDITS = 24
const CHARGE_THRESHOLD = 17

export function PreferencesForm({ parsed, preferences, onPreferencesChange, onPlanGenerated, onBrowseGer, defaultStartTerm, startTermError, onRetryStartTerm }: PreferencesFormProps) {
  const { courses, creditsPerSemester } = preferences
  const startTerm = preferences.startTerm || defaultStartTerm
  const [customDraft, setCustomDraft] = useState(String(creditsPerSemester))
  const [inputValue, setInputValue] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const customInputRef = useRef<HTMLInputElement>(null)

  function addCourse(raw: string) {
    const code = normalizeElective(raw)
    if (!code) {
      setError('Use one specific course code such as CS435 per entry.')
      return false
    }
    if (!courses.includes(code)) onPreferencesChange({ ...preferences, courses: [...courses, code] })
    setError(null)
    return true
  }

  function removeCourse(code: string) {
    onPreferencesChange({ ...preferences, courses: courses.filter((c) => c !== code) })
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if ((e.key === 'Enter' || e.key === ',') && inputValue.trim()) {
      e.preventDefault()
      if (addCourse(inputValue.trim())) setInputValue('')
    } else if (e.key === 'Backspace' && !inputValue && courses.length > 0) {
      onPreferencesChange({ ...preferences, courses: courses.slice(0, -1) })
    }
  }

  function selectPreset(credits: number) {
    onPreferencesChange({ ...preferences, creditsPerSemester: credits })
    setCustomDraft(String(credits))
  }

  function resolveCredits(): number {
    const parsedCredits = parseInt(customDraft, 10)
    return Number.isNaN(parsedCredits)
      ? creditsPerSemester
      : Math.min(MAX_CUSTOM_CREDITS, Math.max(MIN_CUSTOM_CREDITS, parsedCredits))
  }

  function commitCustomCredits() {
    const v = resolveCredits()
    onPreferencesChange({ ...preferences, creditsPerSemester: v })
    setCustomDraft(String(v))
  }

  const isPresetActive = (credits: number) => creditsPerSemester === credits
  const isCustomActive = !PRESET_OPTIONS.some((opt) => opt.credits === creditsPerSemester)

  const customDraftNumber = parseInt(customDraft, 10)
  const showChargeWarning = !Number.isNaN(customDraftNumber) && customDraftNumber > CHARGE_THRESHOLD

  async function handleGenerate() {
    if (isLoading || !startTerm) return
    const pending = inputValue.trim() ? normalizeElective(inputValue) : null
    if (inputValue.trim() && !pending) {
      setError('Use one specific course code such as CS435 per entry.')
      return
    }
    const selectedCourses = pending && !courses.includes(pending) ? [...courses, pending] : courses
    const credits = resolveCredits()
    onPreferencesChange({ courses: selectedCourses, creditsPerSemester: credits, startTerm })
    setInputValue('')
    setCustomDraft(String(credits))
    setIsLoading(true)
    setError(null)
    try {
      const res = await generatePlan(parsed, { courses: selectedCourses, credits_per_semester: credits, start_term: startTerm })
      onPlanGenerated(res.semesters, res.projected_graduation, res.warnings, parsed, startTerm)
    } catch (err) {
      setError(getApiErrorMessage(err, 'Failed to generate plan. Please try again.'))
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface p-6">
      <p className="text-xs font-medium uppercase tracking-wider text-muted mb-5">Preferences</p>

      <div className="flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <label htmlFor="planner-start-term" className="text-xs font-medium uppercase tracking-wider text-muted">Start semester</label>
          <select id="planner-start-term" value={preferences.startTerm || ''}
            onChange={(event) => onPreferencesChange({ ...preferences, startTerm: event.target.value || null })}
            className="w-full rounded-lg border border-border bg-surface-2 p-2 font-mono text-sm text-text">
            <option value="">{defaultStartTerm ? `Default (${planningTermLabel(defaultStartTerm)})` : 'Loading default semester…'}</option>
            {planningTermOptions(defaultStartTerm, preferences.startTerm).map((term) => (
              <option key={term} value={term}>{planningTermLabel(term)}</option>
            ))}
          </select>
          <p className="text-xs text-muted">Plans use spring and fall semesters.</p>
          {startTermError && <div role="status" className="text-xs text-muted">
            {startTermError}{' '}
            <button type="button" onClick={onRetryStartTerm} className="underline underline-offset-2">Retry semester lookup</button>
          </div>}
        </div>
        {/* Electives tag input */}
        <div className="flex flex-col gap-2">
          <p className="text-xs font-medium uppercase tracking-wider text-muted">
            Electives I want to take
          </p>
          <div
            className="min-h-10 flex flex-wrap gap-1.5 p-2 rounded-md border border-border bg-surface-2 cursor-text"
            onClick={() => inputRef.current?.focus()}
          >
            {courses.map((c) => (
              <span
                key={c}
                className="inline-flex items-center gap-1 font-mono text-xs px-2 py-0.5 rounded-md bg-surface border border-border text-text"
              >
                {c}
                <button
                  onClick={(e) => { e.stopPropagation(); removeCourse(c) }}
                  className="text-faint hover:text-text transition-colors duration-150"
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))}
            <input
              ref={inputRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder={courses.length === 0 ? 'e.g. CS375, CS445' : ''}
              className="bg-transparent font-mono text-xs text-text placeholder:text-faint outline-none flex-1 min-w-[8rem]"
            />
          </div>
          {onBrowseGer && (
            <button
              onClick={onBrowseGer}
              className="text-xs text-muted underline underline-offset-2 hover:text-text self-start transition-colors duration-150"
            >
              View available GER Humanities courses →
            </button>
          )}
        </div>

        {/* Credits per semester */}
        <div className="flex flex-col gap-2">
          <p className="text-xs font-medium uppercase tracking-wider text-muted">
            Credits per semester
          </p>
          <div className="grid grid-cols-2 gap-2">
            {PRESET_OPTIONS.map(({ label, credits }) => (
              <button
                key={credits}
                onClick={() => selectPreset(credits)}
                className={[
                  'flex flex-col items-center py-2 rounded-md border text-xs transition-colors duration-150',
                  isPresetActive(credits)
                    ? 'border-njit-red bg-red-dim text-text'
                    : 'border-border bg-surface-2 text-muted hover:border-border-strong hover:text-text',
                ].join(' ')}
              >
                <span className="font-medium">{label}</span>
                <span className="font-mono text-[10px] text-faint">{credits} cr</span>
              </button>
            ))}
            <div
              onClick={() => customInputRef.current?.focus()}
              className={[
                'flex flex-col items-center py-2 rounded-md border border-dashed text-xs cursor-text transition-colors duration-150',
                isCustomActive
                  ? 'border-njit-red bg-red-dim text-text'
                  : 'border-border-strong bg-surface-2 text-muted',
              ].join(' ')}
            >
              <span className="font-medium">Custom</span>
              <input
                ref={customInputRef}
                type="number"
                min={MIN_CUSTOM_CREDITS}
                max={MAX_CUSTOM_CREDITS}
                value={customDraft}
                onChange={(e) => setCustomDraft(e.target.value)}
                onBlur={commitCustomCredits}
                onClick={(e) => e.stopPropagation()}
                aria-label="Custom credits per semester"
                className="w-8 bg-transparent border-0 border-b border-faint text-center font-mono text-[10px] text-text outline-none focus:border-text [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
              />
            </div>
          </div>
          {showChargeWarning && (
            <p className="text-xs text-yellow">
              Credits above 17 may incur an additional charge.
            </p>
          )}
        </div>

        {/* Generate button */}
        <div className="flex flex-col gap-2">
          <button
            onClick={handleGenerate}
            disabled={isLoading || !startTerm}
            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-md bg-njit-red text-white text-sm font-medium disabled:opacity-60 hover:opacity-90 transition-opacity duration-150"
          >
            {isLoading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Generating your plan…
              </>
            ) : (
              'Generate My Plan'
            )}
          </button>
          {error && <p className="text-sm text-njit-red">{error}</p>}
        </div>
      </div>
    </div>
  )
}
