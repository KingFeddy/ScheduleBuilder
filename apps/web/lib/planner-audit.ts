import type { ParsedDegreeValidated } from './api'

const AUDIT_STORAGE_VERSION = 1

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

const text = (value: unknown): value is string => typeof value === 'string'
const nonblank = (value: unknown): value is string => text(value) && value.trim().length > 0
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(nonblank)
const amount = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0
const integer = (value: unknown): value is number => amount(value) && Number.isSafeInteger(value)
const nullableText = (value: unknown) => value === null || text(value)
const nullableAmount = (value: unknown) => value === null || amount(value)
const nullableInteger = (value: unknown) => value === null || integer(value)

function source(value: unknown, requirement = false): boolean {
  if (value === null) return true
  return record(value)
    && (value.document_id === null || (text(value.document_id) && /^[a-f0-9]{64}$/.test(value.document_id)))
    && integer(value.line) && value.line >= 1 && text(value.text)
    && (!requirement || (integer(value.block_index) && value.block_index >= 1))
}

function requirement(value: unknown): boolean {
  if (!record(value) || !nonblank(value.requirement) || !strings(value.options)
    || !text(value.requirement_id) || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value.requirement_id)
    || !nullableAmount(value.remaining_quantity) || !source(value.source, true)
    || !text(value.quantity_unit) || !['classes', 'credits', 'unknown'].includes(value.quantity_unit)) return false
  if (value.quantity_unit === 'classes' && value.remaining_quantity !== null && !integer(value.remaining_quantity)) return false
  const known = value.remaining_quantity !== null && value.quantity_unit !== 'unknown'
  return value.quantity_status === (known ? 'known' : 'unresolved')
}

function attempt(value: unknown): boolean {
  return record(value)
    && text(value.course_code) && /^[A-Z]{2,5}[0-9]{3}[A-Z]?$/.test(value.course_code)
    && (value.grade === null || nonblank(value.grade))
    && nullableAmount(value.credits) && (value.term === null || nonblank(value.term))
    && text(value.status) && ['passed', 'transfer', 'failed', 'withdrawn', 'incomplete', 'in_progress', 'audit', 'unknown'].includes(value.status)
    && (value.earns_credit === null || typeof value.earns_credit === 'boolean')
    && source(value.source)
}

// Check the complete serialized audit shape, including explicit unknowns. This
// protects the UI/storage boundary; the API still validates academic semantics.
export function isCurrentAudit(value: unknown): value is ParsedDegreeValidated {
  if (!record(value) || !nullableText(value.student_name) || !strings(value.majors) || !strings(value.minors)
    || !(value.catalog_year === null || (integer(value.catalog_year) && value.catalog_year >= 1000 && value.catalog_year <= 9999))
    || !nullableInteger(value.credits_completed) || !nullableInteger(value.credits_required) || !nullableInteger(value.credits_remaining)
    || !strings(value.completed_courses) || !strings(value.in_progress_courses)
    || !(value.course_attempts === null || (Array.isArray(value.course_attempts) && value.course_attempts.every(attempt)))
    || !Array.isArray(value.still_needed) || !value.still_needed.every(requirement)) return false
  const ids = value.still_needed.map((item) => item.requirement_id)
  return new Set(ids).size === ids.length
}

export function restoreSavedAudit(raw: string): ParsedDegreeValidated | null {
  try {
    const saved: unknown = JSON.parse(raw)
    if (!record(saved)) return null
    // Compatible unversioned audits can be upgraded without inventing fields.
    const parsed = 'version' in saved
      ? (saved.version === AUDIT_STORAGE_VERSION ? saved.parsed : null)
      : saved
    return isCurrentAudit(parsed) ? parsed : null
  } catch { return null }
}

export function encodeSavedAudit(parsed: ParsedDegreeValidated): string {
  return JSON.stringify({ version: AUDIT_STORAGE_VERSION, parsed })
}
