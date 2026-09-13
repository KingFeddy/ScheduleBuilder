import type { paths } from './api.generated'
import { ApiError, httpError, throwIfCancelled } from './api-errors'

export { ApiError, getApiErrorMessage, isAbortError } from './api-errors'

export interface ApiRequestOptions {
  signal?: AbortSignal | null
}

const BASE = process.env.NEXT_PUBLIC_API_URL || ''

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  throwIfCancelled(init?.signal)
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, init)
  } catch (cause) {
    throwIfCancelled(init?.signal, cause)
    throw new ApiError('network', 'Could not reach the server. Check your connection and try again.', { cause })
  }
  throwIfCancelled(init?.signal, undefined, res)
  let text: string
  try {
    text = await res.text()
  } catch (cause) {
    throwIfCancelled(init?.signal, cause, res)
    if (!res.ok) throw httpError(res, null, cause)
    throw new ApiError('network', 'The response was interrupted. Check your connection and try again.', { status: res.status, cause })
  }
  throwIfCancelled(init?.signal, undefined, res)
  let body: unknown
  try {
    body = JSON.parse(text)
  } catch (cause) {
    if (!res.ok) throw httpError(res, text || null, cause)
    throw new ApiError('invalid-response', 'The server returned an unreadable response. Please try again.', { status: res.status, cause })
  }
  if (!res.ok) throw httpError(res, body)
  return body as T
}

// ─── Types ────────────────────────────────────────────────────────────────────

// Derive aliases from each endpoint so a response-model change cannot leave a
// wrapper quietly pointing at an unrelated schema that still exists.
type CoursesResponse = paths['/api/courses']['get']['responses'][200]['content']['application/json']
type CourseSectionsResponse = paths['/api/courses/{code}/sections']['get']['responses'][200]['content']['application/json']
export type CourseResponse = CoursesResponse[number]
export type CatalogCoverageResponse = paths['/api/catalog/coverage']['get']['responses'][200]['content']['application/json']
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

export function getCatalogCoverage(term: string, options: ApiRequestOptions = {}): Promise<CatalogCoverageResponse> {
  return apiFetch(`/api/catalog/coverage?term=${encodeURIComponent(term)}`, { signal: options.signal })
}

export function getCourses(params: {
  q?: string
  subject?: string
  page?: number
  limit?: number
}, options: ApiRequestOptions = {}): Promise<CoursesResponse> {
  const qs = new URLSearchParams()
  if (params.q) qs.set('q', params.q)
  if (params.subject) qs.set('subject', params.subject)
  if (params.page != null) qs.set('page', String(params.page))
  if (params.limit != null) qs.set('limit', String(params.limit))
  return apiFetch(`/api/courses?${qs}`, { signal: options.signal })
}

export function getCoursesSections(
  code: string,
  term: string,
  options: ApiRequestOptions = {},
): Promise<CourseSectionsResponse> {
  return apiFetch(`/api/courses/${encodeURIComponent(code)}/sections?term=${encodeURIComponent(term)}`, { signal: options.signal })
}

export async function getProfessor(name: string, options: ApiRequestOptions = {}): Promise<ProfessorResponse | null> {
  try {
    const professor = await apiFetch<ProfessorResponse>(
      `/api/professors/${encodeURIComponent(name)}`, { signal: options.signal },
    )
    if (professor === null) throw new ApiError('invalid-response', 'The server returned an unreadable professor response.', { status: 200 })
    return professor
  } catch (error) {
    if (error instanceof ApiError && error.kind === 'http' && error.status === 404) return null
    throw error
  }
}

export function solveSchedule(req: SolveRequest, options: ApiRequestOptions = {}): Promise<SolveResponse> {
  return apiFetch('/api/schedule/solve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
    signal: options.signal,
  })
}

export function parsePlan(
  pdfBase64: string,
  clientPdfHash: string,
  options: ApiRequestOptions = {},
): Promise<ParseResponse> {
  return apiFetch('/api/plan/parse', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pdf_base64: pdfBase64, client_pdf_hash: clientPdfHash }),
    signal: options.signal,
  })
}

export function generatePlan(
  parsedDegree: ParsedDegreeValidated,
  preferences: { courses: string[]; credits_per_semester: number },
  options: ApiRequestOptions = {},
): Promise<GenerateResponse> {
  return apiFetch('/api/plan/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ parsed_degree: parsedDegree, preferences }),
    signal: options.signal,
  })
}

export function getGerCourses(options: ApiRequestOptions = {}): Promise<GerCoursesResponse> {
  return apiFetch('/api/plan/ger-courses', { signal: options.signal })
}

export function getScraperStatus(options: ApiRequestOptions = {}): Promise<ScraperStatusResponse> {
  return apiFetch('/api/scraper/status', { signal: options.signal })
}
