export function isPlanningTerm(value: unknown): value is string {
  return typeof value === 'string' && /^(19|20|21)[0-9]{2}(10|90)$/.test(value)
}

export function planningDefault(value: unknown): string | null {
  if (typeof value !== 'string' || !/^(19|20|21)[0-9]{2}(10|50|90)$/.test(value)) return null
  const term = value.endsWith('50') ? `${value.slice(0, 4)}90` : value
  return isPlanningTerm(term) ? term : null
}

export function planningTermLabel(term: string): string {
  return `${term.endsWith('10') ? 'Spring' : 'Fall'} ${term.slice(0, 4)}`
}

export function planningTermOptions(defaultTerm: string | null, selected: string | null): string[] {
  const anchor = defaultTerm || selected
  if (!anchor) return []
  const year = Number(anchor.slice(0, 4))
  const terms = new Set<string>(selected ? [selected] : [])
  for (let y = Math.max(1900, year - 1); y <= Math.min(2199, year + 6); y++) {
    terms.add(`${y}10`)
    terms.add(`${y}90`)
  }
  return [...terms].sort()
}
