import type { paths } from './api.generated'

const BASE = process.env.NEXT_PUBLIC_API_URL || ''

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init)
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status}: ${detail}`)
  }
  return res.json() as Promise<T>
}

// ─── Types ────────────────────────────────────────────────────────────────────

// Derive aliases from each endpoint so a response-model change cannot leave a
// wrapper quietly pointing at an unrelated schema that still exists.
type CoursesResponse = paths['/api/courses']['get']['responses'][200]['content']['application/json']
type CourseSectionsResponse = paths['/api/courses/{code}/sections']['get']['responses'][200]['content']['application/json']
export type CourseResponse = CoursesResponse[number]
export type CourseDetailResponse = paths['/api/courses/{code}']['get']['responses'][200]['content']['application/json']
export type SectionResponse = CourseSectionsResponse[number]
export type SolveRequest = paths['/api/schedule/solve']['post']['requestBody']['content']['application/json']
export type SolveResponse = paths['/api/schedule/solve']['post']['responses'][200]['content']['application/json']
export type ScheduleResult = SolveResponse['results'][number]
export type SolveSectionResponse = ScheduleResult['sections'][number]
export type Meeting = SolveSectionResponse['meetings'][number]
export type ProfessorResponse = paths['/api/professors/{name}']['get']['responses'][200]['content']['application/json']
export type ParseResponse = paths['/api/plan/parse']['post']['responses'][200]['content']['application/json']
export type ParsedDegreeValidated = ParseResponse['parsed']
export type GenerateResponse = paths['/api/plan/generate']['post']['responses'][200]['content']['application/json']
export type SemesterPlan = GenerateResponse['semesters'][number]
export type PlannedCourse = SemesterPlan['courses'][number]
export type GerCoursesResponse = paths['/api/plan/ger-courses']['get']['responses'][200]['content']['application/json']
export type GerGroup = GerCoursesResponse['groups'][number]
export type ScraperStatusResponse = paths['/api/scraper/status']['get']['responses'][200]['content']['application/json']

// ─── Endpoints ────────────────────────────────────────────────────────────────

export function getCourses(params: {
  q?: string
  subject?: string
  page?: number
  limit?: number
}): Promise<CoursesResponse> {
  const qs = new URLSearchParams()
  if (params.q) qs.set('q', params.q)
  if (params.subject) qs.set('subject', params.subject)
  if (params.page != null) qs.set('page', String(params.page))
  if (params.limit != null) qs.set('limit', String(params.limit))
  return apiFetch(`/api/courses?${qs}`)
}

export function getCoursesSections(
  code: string,
  term: string,
): Promise<CourseSectionsResponse> {
  return apiFetch(`/api/courses/${encodeURIComponent(code)}/sections?term=${term}`)
}

export function getProfessor(name: string): Promise<ProfessorResponse | null> {
  return apiFetch<ProfessorResponse | null>(
    `/api/professors/${encodeURIComponent(name)}`,
  ).catch(() => null)
}

export function solveSchedule(req: SolveRequest): Promise<SolveResponse> {
  return apiFetch('/api/schedule/solve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
}

export function parsePlan(
  pdfBase64: string,
  clientPdfHash: string,
): Promise<ParseResponse> {
  return apiFetch('/api/plan/parse', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pdf_base64: pdfBase64, client_pdf_hash: clientPdfHash }),
  })
}

export function generatePlan(
  parsedDegree: ParsedDegreeValidated,
  preferences: { courses: string[]; credits_per_semester: number },
): Promise<GenerateResponse> {
  return apiFetch('/api/plan/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ parsed_degree: parsedDegree, preferences }),
  })
}

export function getGerCourses(): Promise<GerCoursesResponse> {
  return apiFetch('/api/plan/ger-courses')
}

export function getScraperStatus(): Promise<ScraperStatusResponse> {
  return apiFetch('/api/scraper/status')
}
