import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import type { ProfessorResponse, ScheduleResult } from '@/lib/api'

export interface CommuterOptions {
  compact_week: boolean
  earliest_start: string
  latest_end: string
  minimize_gaps: boolean
  hide_full_sections: boolean
}

// earliest_start/latest_end are hard filters on the backend — a section is
// dropped entirely if it starts before earliest_start or ends after
// latest_end. Defaults must sit outside the real data range (earliest
// scraped class starts 08:30, latest actually ends 22:05 — CHEM/ECE evening
// labs — not 22:00 as first assumed) so an untouched control never silently
// filters anything out.
const defaultCommuterOptions: CommuterOptions = {
  compact_week: false,
  earliest_start: '07:00',
  latest_end: '22:30',
  minimize_gaps: false,
  hide_full_sections: false,
}

interface SchedulerState {
  // persisted
  selectedCourses: string[]
  term: string
  preferredTerm: string | null // null follows the API default on each visit
  commuterOptions: CommuterOptions
  professorPreferences: Record<string, string[]>
  activeResultIndex: number

  // not persisted
  results: ScheduleResult[]
  isLoading: boolean
  error: string | null
  solveWarnings: string[]
  professorCache: Record<string, ProfessorResponse | null>
  professorsByCourse: Record<string, string[]>
  termRevision: number

  // actions
  addCourse: (code: string) => void
  removeCourse: (code: string) => void
  setTerm: (term: string, preference?: string | null) => void
  setCommuterOptions: (opts: Partial<CommuterOptions>) => void
  setProfessorPreferences: (code: string, profs: string[]) => void
  setActiveResultIndex: (i: number) => void
  setResults: (results: ScheduleResult[], warnings: string[]) => void
  setLoading: (loading: boolean) => void
  setError: (error: string | null) => void
  setProfessorCache: (entries: Record<string, ProfessorResponse | null>) => void
  setProfessorsByCourse: (code: string, profs: string[]) => void
}

const safeStorage = {
  getItem: (key: string) => {
    try { return localStorage.getItem(key) } catch { return null }
  },
  setItem: (key: string, value: string) => {
    try { localStorage.setItem(key, value) } catch { /* Safari private mode */ }
  },
  removeItem: (key: string) => {
    try { localStorage.removeItem(key) } catch { /* Safari private mode */ }
  },
}

export const useSchedulerStore = create<SchedulerState>()(
  persist(
    (set) => ({
      // persisted defaults
      selectedCourses: [],
      term: '', // Resolved by term discovery; a persisted value is rechecked first.
      preferredTerm: null,
      commuterOptions: defaultCommuterOptions,
      professorPreferences: {},
      activeResultIndex: 0,

      // not persisted
      results: [],
      isLoading: false,
      error: null,
      solveWarnings: [],
      professorCache: {},
      professorsByCourse: {},
      termRevision: 0,

      // actions
      addCourse: (code) =>
        set((s) => ({
          selectedCourses: s.selectedCourses.includes(code)
            ? s.selectedCourses
            : [...s.selectedCourses, code],
        })),

      removeCourse: (code) =>
        set((s) => ({
          selectedCourses: s.selectedCourses.filter((c) => c !== code),
          professorPreferences: Object.fromEntries(
            Object.entries(s.professorPreferences).filter(([k]) => k !== code),
          ),
        })),

      setTerm: (term, preference = term) => set((s) => s.term === term
        ? { preferredTerm: preference }
        : {
            term, preferredTerm: preference, termRevision: s.termRevision + 1,
            results: [], activeResultIndex: 0, solveWarnings: [], error: null, isLoading: false,
            professorPreferences: {}, professorsByCourse: {},
          }),

      setCommuterOptions: (opts) =>
        set((s) => ({ commuterOptions: { ...s.commuterOptions, ...opts } })),

      setProfessorPreferences: (code, profs) =>
        set((s) => ({
          professorPreferences: profs.length
            ? { ...s.professorPreferences, [code]: profs }
            : Object.fromEntries(
                Object.entries(s.professorPreferences).filter(([k]) => k !== code),
              ),
        })),

      setActiveResultIndex: (i) => set({ activeResultIndex: i }),

      setResults: (results, warnings) =>
        set({ results, solveWarnings: warnings, activeResultIndex: 0, error: null }),

      setLoading: (isLoading) => set({ isLoading }),

      setError: (error) => set({ error }),

      setProfessorCache: (entries) =>
        set((s) => ({ professorCache: { ...s.professorCache, ...entries } })),

      setProfessorsByCourse: (code, profs) =>
        set((s) => ({ professorsByCourse: { ...s.professorsByCourse, [code]: profs } })),
    }),
    {
      name: 'njit-scheduler',
      storage: createJSONStorage(() => safeStorage),
      version: 8,
      partialize: (s) => ({
        selectedCourses: s.selectedCourses,
        term: s.term,
        preferredTerm: s.preferredTerm,
        commuterOptions: s.commuterOptions,
        professorPreferences: s.professorPreferences,
        activeResultIndex: s.activeResultIndex,
      }),
      migrate(state, version) {
        if (version < 6) {
          state = {
            ...(state as Partial<SchedulerState>),
            commuterOptions: defaultCommuterOptions,
          }
        }
        if (version < 7) {
          const s = state as SchedulerState
          state = {
            ...s,
            commuterOptions: { ...s.commuterOptions, minimize_gaps: false },
          }
        }
        if (version < 8) {
          // Earlier versions had no semester chooser: their saved term was an
          // automatic default. Keep it only to identify term-bound preferences;
          // discovery will resolve the new default before any section requests.
          state = { ...(state as Partial<SchedulerState>), preferredTerm: null }
        }
        return state as SchedulerState
      },
    },
  ),
)
