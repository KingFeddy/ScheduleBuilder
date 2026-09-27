import type { CourseResponse } from './api'

export function formatCredits(value: number): string {
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

export function catalogCredits(course: CourseResponse): string {
  if (course.credits_status === 'variable' && course.credits_min != null && course.credits_max != null) {
    const separator = course.credits_options?.length ? ' or ' : '–'
    return `${formatCredits(course.credits_min)}${separator}${formatCredits(course.credits_max)} cr (variable)`
  }
  if (course.credits == null) return 'Credits unknown'
  const suffix = course.credits_status === 'fixed' ? '' : ' (unverified)'
  return `${formatCredits(course.credits)} cr${suffix}`
}

// Preserve technical abbreviations and course sequence numbers when Banner
// supplies an all-caps title. Original API values remain the selection keys.
const titleAcronyms = new Map([
  ...['AI', 'API', 'BIM', 'BME', 'CAD', 'CAM', 'CNC', 'CPU', 'CS', 'ECE', 'GIS',
    'GPU', 'HTML', 'LTC', 'MBA', 'ML', 'NCE', 'NLP', 'SQL', 'ST', 'UI', 'UX',
    'VR', 'AR', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X']
    .map((word): [string, string] => [word, word]),
  ['GENAI', 'GenAI'], ['IOT', 'IoT'],
])

export function formatCourseTitle(title: string | null | undefined): string | null | undefined {
  if (title == null) return title
  let formatted = title
    .replace(/&#39;|&#x27;|&apos;/gi, "'")
    .replace(/&amp;/gi, '&')
    .trim()
    .replace(/\.+$/, '')
    .trimEnd()
  if (formatted === formatted.toUpperCase()) {
    formatted = formatted.replace(/\b[A-Z][A-Z0-9]*\b/g, (word: string, offset: number) => {
      if (offset > 0 && /['’]/.test(formatted[offset - 1])) return word.toLowerCase()
      return titleAcronyms.get(word) ?? word[0] + word.slice(1).toLowerCase()
    })
  }
  // Older catalog cleanup title-cased acronyms too (for example "Ai" and
  // "Iii"). Restore these known spellings in otherwise mixed-case titles.
  return formatted.replace(/\b[A-Za-z][A-Za-z0-9]*\b/g, (word) =>
    titleAcronyms.get(word.toUpperCase()) ?? word,
  )
}

export function formatTopicTitle(title: string): string {
  return formatCourseTitle(title) ?? ''
}
