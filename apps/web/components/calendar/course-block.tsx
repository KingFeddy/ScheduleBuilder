import { courseColor } from '@/lib/course-colors'
import { seatColorClass } from '@/components/ui/seat-status'
import { PX_PER_HOUR, timeToMinutes } from '@/lib/calendar-time'

// Last-name-only — day blocks have no room for a full name. Backend sends
// "Last, First Middle"; null means Banner never reported a professor.
function lastName(raw: string | null): string {
  if (!raw) return 'Staff'
  return raw.includes(',') ? raw.split(',')[0].trim() : raw
}

// One renderable meeting-block instance: a section's identity (course code,
// section number, professor, seats) combined with ONE of that section's
// meeting patterns (time, location). A section with multiple meeting rows
// (e.g. a Monday row and a separate Thursday row at the same CRN) produces
// multiple RenderedMeeting instances — one per row — instead of collapsing
// to a single block. start_time/end_time are non-null here because
// schedule-grid.tsx only ever constructs one of these for a meeting that
// already has both.
export interface RenderedMeeting {
  crn: string
  course_code: string
  section_number: string | null
  topic?: string | null
  section_title?: string | null
  professor_name: string | null
  open_seats: number
  total_seats: number
  start_time: string
  end_time: string
  location: string | null
}

interface CourseBlockProps {
  slot: RenderedMeeting
  startHour: number
  hasConflict?: boolean
}

export function CourseBlock({ slot, startHour, hasConflict = false }: CourseBlockProps) {
  const startMin = timeToMinutes(slot.start_time)
  const endMin = timeToMinutes(slot.end_time)
  const showProfessor = endMin - startMin >= 50
  const topPx = ((startMin - startHour * 60) / 60) * PX_PER_HOUR
  const heightPx = ((endMin - startMin) / 60) * PX_PER_HOUR
  const bg = courseColor(slot.course_code)
  const prof = lastName(slot.professor_name)

  return (
    <div
      className={[
        '@container absolute inset-x-0.5 rounded-md px-2 py-1 overflow-hidden',
        hasConflict ? 'opacity-50 ring-2 ring-njit-red' : '',
      ]
        .join(' ')
        .trim()}
      style={{ top: topPx, height: heightPx, backgroundColor: bg, border: `1px solid ${bg}cc` }}
    >
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-1">
        <p className="col-span-2 row-start-1 @[130px]:col-span-1 font-mono font-bold text-[12px] leading-4 text-text truncate">{slot.course_code}</p>
        {showProfessor && <p className="col-span-2 row-start-2 font-mono text-[11px] leading-[14px] text-text truncate">{prof}</p>}
        <div className={`col-span-2 ${showProfessor ? 'row-start-3' : 'row-start-2'} @[130px]:col-span-1 @[130px]:col-start-2 @[130px]:row-start-1 flex items-baseline justify-between gap-1 font-mono text-[12px] leading-4`}>
          {slot.section_number && <p className="text-muted">{slot.section_number}</p>}
          <p className={`tabular-nums ${seatColorClass(slot.open_seats)}`}>
            {slot.open_seats}/{slot.total_seats}
          </p>
        </div>
      </div>
    </div>
  )
}
