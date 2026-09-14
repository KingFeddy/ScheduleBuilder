import { test, expect } from '@playwright/test'
import { restorePreferences, encodePreferences, normalizeElective } from '../lib/planner-preferences'

test('restores valid legacy preferences and normalizes individual course codes', () => {
  const preferences = { courses: ['CS435', 'MATH211'], creditsPerSemester: 12 }
  expect(restorePreferences(JSON.stringify({ courses: ['cs 435', 'MATH211', 'CS435'], creditsPerSemester: 12 }))).toEqual(preferences)
  expect(restorePreferences(encodePreferences(preferences))).toEqual(preferences)
})

test('rejects invalid saved shapes, versions, elective codes and credit targets', () => {
  for (const raw of ['{', 'null', '[]', '{}', JSON.stringify({ version: 2, courses: [], creditsPerSemester: 12 })]) {
    expect(restorePreferences(raw)).toBeNull()
  }
  for (const courses of [null, 'CS435', [null], ['CS400,CS401'], ['CS4XX']]) {
    expect(restorePreferences(JSON.stringify({ courses, creditsPerSemester: 12 }))).toBeNull()
  }
  for (const creditsPerSemester of ['12', 0, 2, 25, 12.5, null]) {
    expect(restorePreferences(JSON.stringify({ courses: [], creditsPerSemester }))).toBeNull()
  }
})

test('accepts boundary credit targets and rejects malformed typed elective codes', () => {
  for (const creditsPerSemester of [3, 24]) {
    expect(restorePreferences(JSON.stringify({ courses: [], creditsPerSemester }))).toEqual({ courses: [], creditsPerSemester })
  }
  expect(normalizeElective(' cs 435 ')).toBe('CS435')
  expect(normalizeElective('CS400,CS401')).toBeNull()
  expect(normalizeElective('ＣＳ435')).toBeNull()
})
