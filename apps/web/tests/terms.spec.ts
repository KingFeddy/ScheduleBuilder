import { test, expect } from '@playwright/test'
import { useSchedulerStore } from '../store/scheduler'

test.beforeEach(() => useSchedulerStore.setState(useSchedulerStore.getInitialState(), true))

test('has no hardcoded semester before discovery', () => {
  expect(useSchedulerStore.getState().term).toBe('')
  expect(useSchedulerStore.getState().preferredTerm).toBeNull()
})

test('changing semesters clears dependent state but keeps course choices and filters', () => {
  useSchedulerStore.setState({
    term: '202690', selectedCourses: ['CS280'], professorPreferences: { CS280: ['Old Instructor'] },
    professorsByCourse: { CS280: ['Old Instructor'] }, activeResultIndex: 3,
    results: [{ sections: [], campus_days: 0, has_async_sections: false }],
    error: 'Old error', solveWarnings: ['Old warning'], isLoading: true,
  })
  const previous = useSchedulerStore.getState()
  previous.setTerm('202710')
  const next = useSchedulerStore.getState()
  expect(next.term).toBe('202710')
  expect(next.preferredTerm).toBe('202710')
  expect(next.results).toEqual([])
  expect(next.professorPreferences).toEqual({})
  expect(next.professorsByCourse).toEqual({})
  expect(next.activeResultIndex).toBe(0)
  expect(next.isLoading).toBe(false)
  expect(next.solveWarnings).toEqual([])
  expect(next.error).toBeNull()
  expect(next.selectedCourses).toEqual(['CS280'])
  expect(next.commuterOptions).toEqual(previous.commuterOptions)
  expect(next.termRevision).toBe(previous.termRevision + 1)
})

test('following the default keeps same-term preferences and distinguishes a manual choice', () => {
  useSchedulerStore.setState({ term: '202710', professorPreferences: { CS280: ['Same Instructor'] } })
  useSchedulerStore.getState().setTerm('202710', null)
  expect(useSchedulerStore.getState().preferredTerm).toBeNull()
  expect(useSchedulerStore.getState().professorPreferences).toEqual({ CS280: ['Same Instructor'] })
  const revision = useSchedulerStore.getState().termRevision
  useSchedulerStore.getState().setTerm('202710')
  expect(useSchedulerStore.getState().preferredTerm).toBe('202710')
  expect(useSchedulerStore.getState().termRevision).toBe(revision)
})
