export interface PlannerPreferences {
  courses: string[]
  creditsPerSemester: number
}

export const DEFAULT_PREFERENCES: PlannerPreferences = { courses: [], creditsPerSemester: 15 }
export const PREFERENCES_NOTICE = 'Your saved preferences could not be restored. Review your course choices and credit target before generating a plan.'

export function normalizeElective(value: string): string | null {
  if ([...value].some((character) => character.charCodeAt(0) > 127)) return null
  const code = value.toUpperCase().replace(/\s+/g, '')
  return /^[A-Z]{2,5}[0-9]{3}[A-Z]?$/.test(code) ? code : null
}

export function restorePreferences(raw: string): PlannerPreferences | null {
  try {
    const saved: unknown = JSON.parse(raw)
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return null
    if ('version' in saved && saved.version !== 1) return null
    if (!('courses' in saved) || !Array.isArray(saved.courses)
      || !('creditsPerSemester' in saved) || typeof saved.creditsPerSemester !== 'number'
      || !Number.isInteger(saved.creditsPerSemester) || saved.creditsPerSemester < 3 || saved.creditsPerSemester > 24) return null
    const courses: string[] = []
    for (const value of saved.courses) {
      if (typeof value !== 'string') return null
      const code = normalizeElective(value)
      if (!code) return null
      if (!courses.includes(code)) courses.push(code)
    }
    return { courses, creditsPerSemester: saved.creditsPerSemester }
  } catch { return null }
}

export function encodePreferences(preferences: PlannerPreferences): string {
  return JSON.stringify({ version: 1, ...preferences })
}
