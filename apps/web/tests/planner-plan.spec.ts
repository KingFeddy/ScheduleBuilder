import { test, expect } from '@playwright/test'
import { parsedDegree, planResponse } from '../e2e/data'
import { encodeSavedPlan, restoreSavedPlan, isPlanForAudit } from '../lib/planner-plan'

const plan = { semesters: planResponse.semesters, graduation: planResponse.projected_graduation, warnings: planResponse.warnings }

test('restores a plan only for its exact source audit, regardless of object key order', () => {
  const saved = encodeSavedPlan(plan, parsedDegree)
  expect(restoreSavedPlan(saved, parsedDegree)).toEqual(plan)
  expect(restoreSavedPlan(saved, { ...parsedDegree, credits_remaining: 9 })).toBeNull()
  expect(restoreSavedPlan(saved, { ...parsedDegree, still_needed: [{ ...parsedDegree.still_needed[0], options: ['CS350'] }] })).toBeNull()
  expect(restoreSavedPlan(saved, Object.fromEntries(Object.entries(parsedDegree).reverse()) as typeof parsedDegree)).toEqual(plan)
})

test('requires regeneration for unbound legacy, malformed, and future-version saves', () => {
  for (const raw of ['{', 'null', '[]', JSON.stringify(plan),
    JSON.stringify({ ...plan, version: 2, source_audit: parsedDegree })]) {
    expect(restoreSavedPlan(raw, parsedDegree)).toBeNull()
  }
})

test('rejects corrupt warnings, nested rows, duplicate slots, bad terms, and inconsistent totals', () => {
  const semester = plan.semesters[0]
  const course = semester.courses[0]
  const malformed = [
    { ...plan, warnings: null }, { ...plan, warnings: ['okay', {}] }, { ...plan, semesters: [null] },
    ...[{ term: 'garbage' }, { courses: [null] }, { total_credits: 9 },
      { courses: [{ ...course, credits: -1 }] }, { courses: [{ ...course, allocation: {} }] },
      { courses: [{ ...course, requirement: { ...course.requirement, requirement_id: 'other-audit' } }] },
      { courses: [course, course], total_credits: 6 },
    ].map((patch) => ({ ...plan, semesters: [{ ...semester, ...patch }] })),
    { ...plan, semesters: [...plan.semesters].reverse() },
  ]
  for (const candidate of malformed) expect(isPlanForAudit(candidate, parsedDegree)).toBe(false)
})

test('retains partial plans, placeholders, fractional credits and explicit empty warnings', () => {
  const candidate = { ...plan, warnings: [], semesters: [{ ...plan.semesters[0], total_credits: 1.5,
    courses: [{ ...plan.semesters[0].courses[0], course_code: 'TBD', credits: 1.5, credits_estimated: true,
      allocation: { quantity_unit: 'classes', required_quantity: 1, allocated_quantity: 0, unresolved_quantity: 1, status: 'partial' },
    }],
  }] }
  expect(isPlanForAudit(candidate, parsedDegree)).toBe(true)
  expect(isPlanForAudit({ ...plan, semesters: [], graduation: 'Unknown' }, parsedDegree)).toBe(true)
})


test('preserves submitted start context and rejects semesters before it', () => {
  const submitted = { ...plan, startTerm: '202690' }
  expect(restoreSavedPlan(encodeSavedPlan(submitted, parsedDegree), parsedDegree)).toEqual(submitted)
  expect(isPlanForAudit({ ...plan, startTerm: '203190' }, parsedDegree)).toBe(false)
  expect(isPlanForAudit({ ...plan, startTerm: '202650' }, parsedDegree)).toBe(false)
  expect(isPlanForAudit({ ...plan, startTerm: null }, parsedDegree)).toBe(false)
})
