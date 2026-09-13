import type {
  CatalogCoverageResponse, CourseResponse, GenerateResponse, ParsedDegreeValidated, ParseResponse,
  ScraperStatusResponse, SectionResponse, SolveSectionResponse, SolveResponse, TermsResponse,
} from '../lib/api'

// Handwritten, fictional data. These fixtures are not a catalog or a DegreeWorks
// parser acceptance sample; the real API, database, and PDF parser are not used.
export const presentCatalog = { catalog_status: 'present' as const, catalog_note: '' }
export const termDiscovery: TermsResponse = {
  default_term: '202690', terms: [
    { code: '202690', label: 'Fall 2026', has_data: true },
    { code: '202710', label: 'Spring 2027', has_data: true },
    { code: '202750', label: 'Summer 2027', has_data: true },
  ],
}
export const catalogCoverage: CatalogCoverageResponse = {
  term: '202690', configured_subjects: ['CS', 'HUM'], elective_subjects: ['HUM'], warnings: [],
  subjects: [
    { subject: 'CS', configured: true, course_count: 1, section_count: 1 },
    { subject: 'HUM', configured: true, course_count: 1, section_count: 1 },
  ],
}
export const gerCoverage = { subjects: ['HUM'], missing_subjects: [], unconfigured_subjects: [], warnings: [] }
export const courses: CourseResponse[] = [
  { ...presentCatalog, course_code: 'CS280', title: 'Programming Language Concepts', credits: 3,
    title_status: 'verified', credits_status: 'fixed', credits_min: 3, credits_max: 3, credits_options: [], metadata_warnings: [] },
  { ...presentCatalog, course_code: 'HUM101', title: 'Writing and Communication', credits: 3,
    title_status: 'verified', credits_status: 'fixed', credits_min: 3, credits_max: 3, credits_options: [], metadata_warnings: [] },
]

export const sections: SolveSectionResponse[] = [
  {
    crn: '99001', term: '202690', course_code: 'CS280', section_number: '001',
    professor_name: 'Test Lecturer, Taylor', total_seats: 30, open_seats: 12,
    scraped_at: '2026-09-12T14:55:00Z',
    meetings: [
      { days: 'M', start_time: '09:00:00', end_time: '10:20:00', location: 'TEST 101' },
      { days: 'R', start_time: '13:00:00', end_time: '14:20:00', location: 'TEST 102' },
    ],
  },
  {
    crn: '99002', term: '202690', course_code: 'HUM101', section_number: '851',
    professor_name: 'Instructor, Test', total_seats: 20, open_seats: 5,
    scraped_at: '2026-09-12T14:55:00Z',
    meetings: [{ days: null, start_time: null, end_time: null, location: 'Online' }],
  },
]

export const solveResponse: SolveResponse = {
  results: [{ sections, campus_days: 2, has_async_sections: true }],
  warnings: ['Synthetic schedule warning: verify registration availability.'],
  truncated: false,
}

// The section-list endpoint deliberately omits solve-only identifiers.
export const sectionList: SectionResponse[] = sections.map((section) => ({
  crn: section.crn, course_code: section.course_code, professor_name: section.professor_name,
  total_seats: section.total_seats, open_seats: section.open_seats,
  scraped_at: section.scraped_at, meetings: section.meetings,
}))

export const scraperStatus: ScraperStatusResponse = {
  last_scrape: '2026-09-12T14:55:00Z', status: 'completed',
  sections_upserted: 2, error_message: null,
}

export const parsedDegree: ParsedDegreeValidated = {
  student_name: 'Synthetic Test Student', majors: ['Computer Science'], minors: [],
  catalog_year: 2024, credits_completed: 114, credits_required: 120, credits_remaining: 6,
  completed_courses: ['CS100', 'CS113'], in_progress_courses: [],
  still_needed: [
    { requirement: 'Programming languages', options: ['CS280'] },
    { requirement: 'Writing elective', options: ['HUM101'] },
  ],
}

export const planResponse: GenerateResponse = {
  semesters: [
    {
      term: '202690', term_label: 'Fall 2026', total_credits: 3,
      courses: [{ ...presentCatalog, course_code: 'CS280', title: courses[0].title, credits: 3, badge: 'Required', reason: '', credits_estimated: false, credits_note: '', title_status: 'verified' }],
    },
    {
      term: '202710', term_label: 'Spring 2027', total_credits: 3,
      courses: [{ ...presentCatalog, course_code: 'HUM101', title: courses[1].title, credits: 3, badge: 'Elective', reason: 'Synthetic writing elective', credits_estimated: false, credits_note: '', title_status: 'verified' }],
    },
  ],
  projected_graduation: 'Spring 2027',
  warnings: ['Synthetic plan warning: confirm this advisory plan with an advisor.'],
}

export const parseResponse: ParseResponse = { parsed: parsedDegree, server_hash: '', warnings: [] }

// Upload-shaped bytes above the UI's 5 KiB minimum. The mocked parse endpoint
// never reads a real PDF; this deliberately contains no actual student data.
export const syntheticPdf = {
  name: 'synthetic-degree-audit.pdf',
  mimeType: 'application/pdf',
  buffer: Buffer.from(`%PDF-1.4\n${'% Synthetic browser upload fixture only.\n'.repeat(160)}%%EOF\n`),
}
