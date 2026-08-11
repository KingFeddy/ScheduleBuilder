'use client'

import { useState, useEffect, useRef, type KeyboardEvent } from 'react'
import { X, Loader2 } from 'lucide-react'
import { generatePlan, type ParsedDegreeValidated, type SemesterPlan } from '@/lib/api'

interface PreferencesFormProps {
  parsed: ParsedDegreeValidated
  onPlanGenerated: (semesters: SemesterPlan[], graduation: string, warnings: string[]) => void
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

export function PreferencesForm({ parsed, onPlanGenerated, onBrowseGer }: PreferencesFormProps) {
  const [courses, setCourses] = useState<string[]>([])
  const [creditsPerSemester, setCreditsPerSemester] = useState(15)
  const [customDraft, setCustomDraft] = useState('15')
  const [inputValue, setInputValue] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const customInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    try {
      const saved = localStorage.getItem('njit-dw-preferences')
      if (saved) {
        const prefs = JSON.parse(saved) as { courses: string[]; creditsPerSemester: number }
        const loaded = prefs.creditsPerSemester || 15
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setCourses(prefs.courses || [])
        setCreditsPerSemester(loaded)
        setCustomDraft(String(loaded))
      }
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem('njit-dw-preferences', JSON.stringify({ courses, creditsPerSemester }))
    } catch { /* ignore */ }
  }, [courses, creditsPerSemester])

  function addCourse(raw: string) {
    const code = raw.toUpperCase().replace(/\s+/g, '')
    if (code && !courses.includes(code)) {
      setCourses((prev) => [...prev, code])
    }
  }

  function removeCourse(code: string) {
    setCourses((prev) => prev.filter((c) => c !== code))
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if ((e.key === 'Enter' || e.key === ',') && inputValue.trim()) {
      e.preventDefault()
      addCourse(inputValue.trim())
      setInputValue('')
    } else if (e.key === 'Backspace' && !inputValue && courses.length > 0) {
      setCourses((prev) => prev.slice(0, -1))
    }
  }

  function selectPreset(credits: number) {
    setCreditsPerSemester(credits)
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
    setCreditsPerSemester(v)
    setCustomDraft(String(v))
  }

  const isPresetActive = (credits: number) => creditsPerSemester === credits
  const isCustomActive = !PRESET_OPTIONS.some((opt) => opt.credits === creditsPerSemester)

  const customDraftNumber = parseInt(customDraft, 10)
  const showChargeWarning = !Number.isNaN(customDraftNumber) && customDraftNumber > CHARGE_THRESHOLD

  async function handleGenerate() {
    if (isLoading) return
    const credits = resolveCredits()
    setCreditsPerSemester(credits)
    setCustomDraft(String(credits))
    setIsLoading(true)
    setError(null)
    try {
      const res = await generatePlan(parsed, { courses, credits_per_semester: credits })
      try {
        localStorage.setItem(
          'njit-dw-plan',
          JSON.stringify({ semesters: res.semesters, graduation: res.projected_graduation }),
        )
      } catch { /* ignore */ }
      onPlanGenerated(res.semesters, res.projected_graduation, res.warnings)
    } catch (err) {
      const msg = err instanceof Error ? err.message : ''
      if (msg.includes('429')) {
        setError('Too many requests — please wait a moment and try again.')
      } else {
        setError('Failed to generate plan. Please try again.')
      }
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface p-6">
      <p className="text-xs font-medium uppercase tracking-wider text-muted mb-5">Preferences</p>

      <div className="flex flex-col gap-5">
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
            disabled={isLoading}
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
