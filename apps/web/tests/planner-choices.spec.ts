import { test, expect } from '@playwright/test'
import { parsedDegree, planResponse, replacementPlan } from '../e2e/data'
import { matchesRequirement, replacementChoices, validChoices } from '../lib/planner-choices'
import { encodeSavedPlan, restoreSavedPlan, isPlanForAudit } from '../lib/planner-plan'

const choices = { 'req-writing': ['HUM201'] }
const plan = { semesters: replacementPlan.semesters, graduation: replacementPlan.projected_graduation,
  warnings: replacementPlan.warnings, requirementChoices: choices }

test('choices survive reload only with the original audit and matching assigned courses', () => {
  expect(restoreSavedPlan(encodeSavedPlan(plan, parsedDegree), parsedDegree)).toEqual(plan)
  expect(isPlanForAudit({ ...plan, semesters: planResponse.semesters }, parsedDegree)).toBe(false)
  expect(restoreSavedPlan(encodeSavedPlan(plan, parsedDegree), { ...parsedDegree, credits_remaining: 9 })).toBeNull()
  const duplicate = { ...plan, semesters: [...plan.semesters, { ...plan.semesters[1], term: '202890', courses: [
    { ...plan.semesters[1].courses[0], slot_id: 'extra', requirement: null },
  ] }] }
  expect(isPlanForAudit(duplicate, parsedDegree)).toBe(false)
})

test('saved choices reject stale IDs, duplicates, malformed courses, history and excess quantities', () => {
  for (const value of [null, [], { stale: ['HUM201'] }, { 'req-writing': [] }, { 'req-writing': ['hum201'] },
    { 'req-writing': ['HUM201', 'HUM201'] }, { 'req-writing': ['CS280'] }, { 'req-writing': ['HUM101', 'HUM201'] }]) {
    expect(validChoices(value, parsedDegree)).toBe(false)
  }
  expect(validChoices(choices, { ...parsedDegree, completed_courses: ['HUM201'] })).toBe(false)
  expect(validChoices(choices, { ...parsedDegree, in_progress_courses: ['HUM201'] })).toBe(false)
})

test('matching uses exact codes, digit-position wildcards and universal options only', () => {
  const requirement = parsedDegree.still_needed[0]
  for (const [option, code, expected] of [['CS3XX', 'CS300', true], ['CS3XX', 'CS400', false],
    ['CS3X', 'CS300', false], ['CS3XX|HUM201', 'CS300', false], ['@', 'HUM201', true], ['@', 'TBD', false]] as const) {
    expect(matchesRequirement(code, { ...requirement, options: [option] })).toBe(expected)
  }
})

test('replacement retains siblings across semesters and other existing choices', () => {
  const requirement = { ...parsedDegree.still_needed[1], remaining_quantity: 2, options: ['HUM101', 'HUM102', 'HUM201'] }
  const semesters = planResponse.semesters.map((semester, i) => ({ ...semester, courses: [
    { ...semester.courses[0], requirement, course_code: i === 0 ? 'HUM101' : 'HUM102' },
  ] }))
  expect(replacementChoices(semesters, { other: ['CS300'] }, 'slot-programming', 'HUM201'))
    .toEqual({ other: ['CS300'], 'req-writing': ['HUM201', 'HUM102'] })
  expect(() => replacementChoices(semesters, {}, 'slot-programming', 'HUM102')).toThrow('already in your plan')
})

test('choosing a TBD replaces only that slot and does not submit other placeholders as courses', () => {
  const requirement = { ...parsedDegree.still_needed[1], remaining_quantity: 2 }
  const semesters = planResponse.semesters.map((semester) => ({ ...semester, courses: [
    { ...semester.courses[0], requirement, course_code: 'TBD' },
  ] }))
  expect(replacementChoices(semesters, {}, 'slot-programming', 'HUM201')).toEqual(choices)
})
