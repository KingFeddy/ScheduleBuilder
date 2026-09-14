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
