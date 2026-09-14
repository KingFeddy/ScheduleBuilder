import type { ParsedDegreeValidated, SemesterPlan } from './api'
import { isCurrentAudit } from './planner-audit'
import { isPlanningTerm } from './planner-terms'

export interface PlanState {
  semesters: SemesterPlan[]
  graduation: string
  warnings: string[]
  startTerm?: string
}

export const SAVED_PLAN_NOTICE = 'Your saved plan is outdated, damaged, or belongs to a different audit. Generate a new plan to continue.'

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
const text = (value: unknown): value is string => typeof value === 'string'
const nonblank = (value: unknown): value is string => text(value) && value.trim().length > 0
const amount = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0
const nullableAmount = (value: unknown) => value === null || amount(value)
const oneOf = (value: unknown, options: string[]) => text(value) && options.includes(value)

// Object property order is irrelevant; array order and every audit field matter.
// The source snapshot stays local alongside the plan, with no hash dependency.
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (record(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
  return JSON.stringify(value)
}

export function sameAudit(a: ParsedDegreeValidated, b: ParsedDegreeValidated): boolean {
  return canonical(a) === canonical(b)
}

export function isPlanForAudit(value: unknown, audit: ParsedDegreeValidated): value is PlanState {
  if (!record(value) || !nonblank(value.graduation) || !Array.isArray(value.warnings)
    || !value.warnings.every(text) || !Array.isArray(value.semesters)) return false
  if (value.startTerm !== undefined && !isPlanningTerm(value.startTerm)) return false
  const requirements = new Map(audit.still_needed.map((item) => [item.requirement_id, canonical(item)]))
  const slots = new Set<string>()
  let previousTerm = ''
  for (const semester of value.semesters) {
    if (!record(semester) || !text(semester.term) || !/^[1-9][0-9]{3}(10|50|90)$/.test(semester.term)
      || (typeof value.startTerm === 'string' && semester.term < value.startTerm)
      || semester.term <= previousTerm || !nonblank(semester.term_label)
      || !amount(semester.total_credits) || !Array.isArray(semester.courses)) return false
    previousTerm = semester.term
    let credits = 0
    for (const course of semester.courses) {
      if (!record(course) || !text(course.slot_id) || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(course.slot_id)
        || slots.has(course.slot_id) || !nonblank(course.course_code)
        || !(course.title === null || text(course.title)) || !amount(course.credits)
        || typeof course.credits_estimated !== 'boolean' || !text(course.credits_note) || !text(course.reason)
        || !oneOf(course.title_status, ['verified', 'unverified', 'missing'])
        || !oneOf(course.catalog_status, ['present', 'subject_not_configured', 'course_missing', 'unresolved', 'unknown'])
        || !text(course.catalog_note) || !oneOf(course.badge, ['Required', 'Elective', 'TBD'])) return false
      slots.add(course.slot_id)
      if (course.requirement !== null) {
        if (!record(course.requirement) || !text(course.requirement.requirement_id)
          || requirements.get(course.requirement.requirement_id) !== canonical(course.requirement)) return false
      }
      if (course.allocation !== null) {
        const allocation = course.allocation
        if (!record(allocation) || course.requirement === null
          || !nullableAmount(allocation.required_quantity) || !nullableAmount(allocation.allocated_quantity)
          || !nullableAmount(allocation.unresolved_quantity)
          || !oneOf(allocation.quantity_unit, ['classes', 'credits', 'unknown'])
          || !oneOf(allocation.status, ['allocated', 'partial', 'unknown'])) return false
      }
      credits += course.credits
    }
    if (!Number.isFinite(credits) || Math.round(credits * 100) !== Math.round(semester.total_credits * 100)) return false
  }
  return true
}

export function encodeSavedPlan(plan: PlanState, audit: ParsedDegreeValidated): string {
  return JSON.stringify({ version: 1, source_audit: audit, ...plan })
}

export function restoreSavedPlan(raw: string, audit: ParsedDegreeValidated): PlanState | null {
  try {
    const saved: unknown = JSON.parse(raw)
    if (!record(saved) || saved.version !== 1 || !isCurrentAudit(saved.source_audit)
      || !sameAudit(saved.source_audit, audit) || !isPlanForAudit(saved, audit)) return null
    return { semesters: saved.semesters, graduation: saved.graduation, warnings: saved.warnings,
      ...(saved.startTerm ? { startTerm: saved.startTerm } : {}) }
  } catch { return null }
}
