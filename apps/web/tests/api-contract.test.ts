// Compile-time regressions for JSON shapes actually returned by the API.
// These fixtures must remain valid without pretending nulls or absent keys exist.
import type {
  CourseResponse, GenerateRequest, GerGroup, ParsedDegreeValidated, PlanPreferences, ProfessorResponse,
  SolveRequest, SolveResponse,
} from '../lib/api'
import type { getCoursesSections, getScraperStatus } from '../lib/api'

export const returnedSections: Awaited<ReturnType<typeof getCoursesSections>> = [{
  crn: '99001', course_code: 'CS280', professor_name: null,
  total_seats: 30, open_seats: 0, scraped_at: null,
  meetings: [{ days: null, start_time: null, end_time: null, location: null }],
}]

// Truncation describes the entire search, including searches with zero results.
export const truncatedSearch: SolveResponse = { results: [], warnings: [], truncated: true }

export const unnamedCourse: CourseResponse = { course_code: 'CS280', title: null, credits: null,
  catalog_status: 'present', catalog_note: '',
  title_status: 'missing', credits_status: 'missing', credits_min: null, credits_max: null, credits_options: [], metadata_warnings: [] }
export const unknownProfessor: ProfessorResponse = {
  rmp_score: null, rmp_difficulty: null, rmp_would_take_again: null,
  rmp_num_ratings: null, rmp_tags: [], department: null,
}
export const unnamedGer: GerGroup = { prefix: 'HUM', courses: [{ code: 'HUM101', title: null, title_status: 'missing', catalog_status: 'present', catalog_note: '' }] }
export const incompleteDegree: ParsedDegreeValidated = {
  student_name: null, majors: ['Computer Science'], minors: [], catalog_year: null,
  credits_completed: null, credits_required: null, credits_remaining: null,
  completed_courses: [], in_progress_courses: [], still_needed: [{
    requirement_id: 'req-synthetic', requirement: 'Unresolved elective', options: [],
    remaining_quantity: null, quantity_unit: 'unknown', quantity_status: 'unresolved', source: null,
  }],
}

export const defaultPlanRequest: GenerateRequest = { parsed_degree: incompleteDegree, preferences: {} }
export const selectedPlanPreferences: PlanPreferences = { courses: ['CS435'], credits_per_semester: 15 }

// @ts-expect-error Generation preferences require individual string course codes.
export const malformedPlanCourses: PlanPreferences = { courses: [435] }
// @ts-expect-error The credit target must be a number, not a stored input string.
export const malformedPlanTarget: PlanPreferences = { credits_per_semester: '15' }
// @ts-expect-error Misspelled preference keys cannot silently use defaults.
export const misspelledPlanPreferences: PlanPreferences = { electives: ['CS435'] }
// @ts-expect-error Degree course history must be a list of strings.
export const malformedPlanDegree: GenerateRequest = { parsed_degree: { completed_courses: [435] }, preferences: {} }
export const neverScraped: Awaited<ReturnType<typeof getScraperStatus>> = {
  term: '202690', status: 'never_run', checked_at: '2026-09-13T12:00:00Z',
  latest_attempt: null, last_successful_refresh: null, data_as_of: null,
  section_count: 0, sections_missing_timestamps: 0,
}

// Backend defaults make options optional; explicit null bounds are also accepted.
export const minimalSolve: SolveRequest = { course_codes: ['CS280'], term: '202690' }
export const unboundedSolve: SolveRequest = {
  ...minimalSolve, options: { earliest_start: null, latest_end: null, blocked_days: ['S'] },
}

export function verifyFieldLocations(response: SolveResponse) {
  const truncated: boolean = response.truncated
  // @ts-expect-error The search flag is not repeated on individual schedules.
  void response.results[0].truncated
  // @ts-expect-error Section-list payloads do not include the solved section's term.
  void returnedSections[0].term
  // @ts-expect-error Section numbers exist only on solved-section responses.
  void returnedSections[0].section_number
  return truncated
}
