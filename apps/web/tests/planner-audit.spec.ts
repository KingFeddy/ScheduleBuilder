import { test, expect } from '@playwright/test'
import { parsedDegree } from '../e2e/data'
import { encodeSavedAudit, restoreSavedAudit, isCurrentAudit } from '../lib/planner-audit'

test('restores complete legacy audits and writes a versioned record without changing their data', () => {
  expect(restoreSavedAudit(JSON.stringify(parsedDegree))).toEqual(parsedDegree)
  const saved = encodeSavedAudit(parsedDegree)
  expect(JSON.parse(saved)).toEqual({ version: 1, parsed: parsedDegree })
  expect(restoreSavedAudit(saved)).toEqual(parsedDegree)
})

test('preserves explicit unknown quantities rather than inventing an amount', () => {
  const audit = { ...parsedDegree, still_needed: [{ ...parsedDegree.still_needed[0],
    remaining_quantity: null, quantity_unit: 'unknown', quantity_status: 'unresolved',
  }] }
  expect(restoreSavedAudit(JSON.stringify(audit))).toEqual(audit)
})

test('rejects old requirements that omit quantity evidence even inside a current envelope', () => {
  const audit = { ...parsedDegree, still_needed: [{ requirement: 'Synthetic class', options: ['CS280'] }] }
  expect(restoreSavedAudit(JSON.stringify(audit))).toBeNull()
  expect(restoreSavedAudit(JSON.stringify({ version: 1, parsed: audit }))).toBeNull()
})

test('rejects corrupt JSON, wrong shapes, and unsupported versions', () => {
  for (const raw of ['{', 'null', '[]', '42', '{}', JSON.stringify({ version: 2, parsed: parsedDegree })]) {
    expect(restoreSavedAudit(raw)).toBeNull()
  }
  for (const patch of [{ majors: null }, { credits_remaining: '6' }, { completed_courses: [null] },
    { credits_completed: -1 }, { catalog_year: 12 }, { course_attempts: [null] }]) {
    expect(restoreSavedAudit(JSON.stringify({ ...parsedDegree, ...patch }))).toBeNull()
  }
  expect(isCurrentAudit({ ...parsedDegree, credits_remaining: Infinity })).toBe(false)
})

test('rejects inconsistent quantities, duplicate IDs, and invalid source records', () => {
  const requirement = parsedDegree.still_needed[0]
  for (const patch of [{ remaining_quantity: -1 }, { remaining_quantity: 1.5 },
    { quantity_status: 'unresolved' }, { quantity_unit: 'unknown' },
    { source: { document_id: null, block_index: 1, line: 0, text: 'Synthetic source' } }]) {
    expect(restoreSavedAudit(JSON.stringify({ ...parsedDegree, still_needed: [{ ...requirement, ...patch }] }))).toBeNull()
  }
  expect(restoreSavedAudit(JSON.stringify({ ...parsedDegree, still_needed: [requirement, requirement] }))).toBeNull()
})

test('preserves graded attempts and explicit missing metadata but rejects invalid attempt fields', () => {
  const attempt = { course_code: 'CS100', grade: 'B+', credits: 3, term: '2025 Spring',
    status: 'passed', earns_credit: true,
    source: { document_id: 'a'.repeat(64), line: 3, text: 'Synthetic attempt' },
  }
  const audit = { ...parsedDegree, student_name: null, catalog_year: null,
    credits_completed: null, credits_remaining: null, credits_required: null, course_attempts: [attempt] }
  expect(restoreSavedAudit(JSON.stringify(audit))).toEqual(audit)
  for (const patch of [{ course_code: null }, { credits: '3' }, { status: 'invented' },
    { earns_credit: 'yes' }, { source: { ...attempt.source, document_id: 'not-a-hash' } }]) {
    expect(restoreSavedAudit(JSON.stringify({ ...audit, course_attempts: [{ ...attempt, ...patch }] }))).toBeNull()
  }
})
