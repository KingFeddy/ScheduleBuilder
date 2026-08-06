import type { SectionSlot } from '@/lib/api'
import { courseColor } from '@/lib/course-colors'
import { seatColorClass } from '@/components/ui/seat-status'

export function isAsyncSection(s: SectionSlot): boolean {
  return s.meetings.every((m) => !m.days || !m.start_time || !m.end_time)
}

// Full name here, unlike CourseBlock's lastName — this block isn't squeezed
// into a day column, so there's room to be more informative.
function fullName(raw: string | null): string {
  if (!raw) return 'Staff'
  if (!raw.includes(',')) return raw
  const [last, first = ''] = raw.split(', ')
  return `${first} ${last}`.trim()
}

export function AsyncBlock({ slot }: { slot: SectionSlot }) {
  const bg = courseColor(slot.course_code)
  const prof = fullName(slot.professor_name)

  return (
    <div
      className="w-56 flex-shrink-0 rounded-md px-3 py-2"
      style={{ backgroundColor: bg, border: `1px solid ${bg}cc` }}
    >
      <div className="flex items-baseline justify-between gap-1">
        <div className="flex items-baseline gap-1.5 min-w-0">
          <p className="font-mono font-bold text-[15px] text-text truncate">{slot.course_code}</p>
          {slot.section_number && (
            <p className="font-mono text-[11px] text-muted flex-shrink-0">§{slot.section_number}</p>
          )}
        </div>
        <p
          className={`font-mono tabular-nums text-[12px] flex-shrink-0 ${seatColorClass(slot.open_seats)}`}
        >
          {slot.open_seats}/{slot.total_seats}
        </p>
      </div>
      <p className="font-mono text-[13px] text-text truncate">{prof}</p>
    </div>
  )
}
