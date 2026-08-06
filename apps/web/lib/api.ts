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

export interface CourseResponse {
  course_code: string
  title: string
  credits: number
}

export interface SectionSlot {
  crn: string
  term: string
  course_code: string
  professor_name: string | null
  total_seats: number
  open_seats: number
  scraped_at: string | null
  days: string | null
  start_time: string | null
  end_time: string | null
  location: string | null
  section_number: string | null
}

export interface ScheduleResult {
  sections: SectionSlot[]
  gap_minutes: number
  campus_days: number
  has_async_sections: boolean
  truncated: boolean
}

export interface SolveRequest {
  course_codes: string[]
  term: string
  options: {
    earliest_start: string
    latest_end: string
    minimize_gaps: boolean
  }
  compact_week: boolean
  professor_preferences: Record<string, string[]>
}

export interface SolveResponse {
  results: ScheduleResult[]
  warnings: string[]
}

export interface ProfessorResponse {
  rmp_score: number | null
  rmp_difficulty: number | null
  rmp_would_take_again: number | null
  rmp_num_ratings: number | null
  rmp_tags: string[]
  department: string
}

export interface ParsedDegreeValidated {
  student_name: string
  majors: string[]
  minors: string[]
  catalog_year: number
  credits_completed: number
  credits_required: number
  credits_remaining: number
  completed_courses: string[]
  in_progress_courses: string[]
  still_needed: { requirement: string; options: string[] }[]
}

export interface PlannedCourse {
  course_code: string
  title: string | null
  credits: number
  badge: 'Required' | 'Elective' | 'TBD'
  reason: string
}

export interface SemesterPlan {
  term: string
  term_label: string
  courses: PlannedCourse[]
  total_credits: number
}

export interface GerGroup {
  prefix: string
  courses: { code: string; title: string }[]
}

// ─── Endpoints ────────────────────────────────────────────────────────────────

export function getCourses(params: {
  q?: string
  subject?: string
  page?: number
  limit?: number
}): Promise<CourseResponse[]> {
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
): Promise<SectionSlot[]> {
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
): Promise<{ parsed: ParsedDegreeValidated; server_hash: string; warnings: string[] }> {
  return apiFetch('/api/plan/parse', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pdf_base64: pdfBase64, client_pdf_hash: clientPdfHash }),
  })
}

export function generatePlan(
  parsedDegree: ParsedDegreeValidated,
  preferences: { courses: string[]; credits_per_semester: number },
): Promise<{ semesters: SemesterPlan[]; projected_graduation: string; warnings: string[] }> {
  return apiFetch('/api/plan/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ parsed_degree: parsedDegree, preferences }),
  })
}

export function getGerCourses(): Promise<{ groups: GerGroup[] }> {
  return apiFetch('/api/plan/ger-courses')
}

export function getScraperStatus(): Promise<{
  last_scrape: string | null
  status: string | null
  sections_upserted: number
  error_message: string | null
}> {
  return apiFetch('/api/scraper/status')
}
