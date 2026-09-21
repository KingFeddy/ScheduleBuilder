import type { ParsedDegreeValidated, SemesterPlan, StillNeededItem } from './api'
import { normalizeElective } from './planner-preferences'

export type RequirementChoices = Record<string, string[]>

export function matchesRequirement(code: string, requirement: StillNeededItem): boolean {
  if (normalizeElective(code) !== code) return false
  return requirement.options.some((option) => option === '@'
    || (/^[A-Z]{2,5}[0-9X]{3}[A-Z]?$/.test(option)
      && new RegExp(`^${option.replaceAll('X', '[0-9]')}$`).test(code)))
}

export function canChoose(requirement: StillNeededItem | null | undefined): boolean {
  return !!requirement && !(requirement.quantity_status === 'known' && requirement.remaining_quantity === 0)
    && requirement.options.some((option) => option === '@' || /^[A-Z]{2,5}[0-9X]{3}[A-Z]?$/.test(option))
}

// Saved choices are audit-scoped input, not proof of current catalog eligibility.
export function validChoices(value: unknown, audit: ParsedDegreeValidated): value is RequirementChoices {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const entries = Object.entries(value)
  if (entries.length > 200) return false
  const seen = new Set([...audit.completed_courses, ...audit.in_progress_courses])
  let count = 0
  for (const [id, codes] of entries) {
    const requirement = audit.still_needed.find((item) => item.requirement_id === id)
    if (!requirement || !canChoose(requirement) || !Array.isArray(codes) || !codes.length) return false
    if (requirement.quantity_status !== 'known' && codes.length > 1) return false
    if (requirement.quantity_unit === 'classes' && requirement.remaining_quantity !== null
      && codes.length > requirement.remaining_quantity) return false
    for (const code of codes) {
      if (typeof code !== 'string' || seen.has(code) || !matchesRequirement(code, requirement) || ++count > 200) return false
      seen.add(code)
    }
  }
  return true
}

export function replacementChoices(semesters: SemesterPlan[], previous: RequirementChoices, slotId: string, newCode: string): RequirementChoices {
  const rows = semesters.flatMap((semester) => semester.courses)
  const selected = rows.find((row) => row.slot_id === slotId)
  if (!selected?.requirement || !matchesRequirement(newCode, selected.requirement)) throw new Error('Choose a course from this requirement’s options.')
  if (rows.some((row) => row.slot_id !== slotId && row.course_code === newCode)) throw new Error('This course is already in your plan.')
  const id = selected.requirement.requirement_id
  const codes = rows.filter((row) => row.requirement?.requirement_id === id)
    .map((row) => row.slot_id === slotId ? newCode : row.course_code)
    .filter((code) => normalizeElective(code) === code)
  return { ...previous, [id]: codes }
}
