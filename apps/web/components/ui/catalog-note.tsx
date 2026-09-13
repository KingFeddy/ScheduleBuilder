import type { PlannedCourse } from '@/lib/api'

const labels = {
  subject_not_configured: 'Subject outside collection scope.',
  course_missing: 'Course missing from catalog.',
  unresolved: 'Catalog course unresolved.',
  unknown: 'Catalog coverage unchecked.',
}

export function CatalogNote({ status, note }: {
  status?: PlannedCourse['catalog_status']
  note?: string
}) {
  if (status === 'present') return null
  return (
    <span className="block text-xs text-yellow">
      <span className="block">{labels[status || 'unknown'] || labels.unknown}</span>
      <span className="block">{note || 'Regenerate the plan to check catalog coverage.'}</span>
    </span>
  )
}
