import { courseColor } from '@/lib/course-colors'
import { seatColorClass } from '@/components/ui/seat-status'

const PX_PER_HOUR = 48
const START_HOUR = 7

function timeToMinutes(timeStr: string): number {
  const ampm = /(\d+):(\d+)\s*(AM|PM)/i.exec(timeStr)
  if (ampm) {
    let h = parseInt(ampm[1], 10)
    const m = parseInt(ampm[2], 10)
    if (ampm[3].toUpperCase() === 'PM' && h !== 12) h += 12
    if (ampm[3].toUpperCase() === 'AM' && h === 12) h = 0
    return h * 60 + m
  }
  const parts = timeStr.split(':')
  return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10)
}

function minutesToPx(totalMinutes: number): number {
  return ((totalMinutes - START_HOUR * 60) / 60) * PX_PER_HOUR
}

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
  professor_name: string | null
  open_seats: number
  total_seats: number
  start_time: string
  end_time: string
  location: string | null
}

interface CourseBlockProps {
  slot: RenderedMeeting
  hasConflict?: boolean
}

export function CourseBlock({ slot, hasConflict = false }: CourseBlockProps) {
  const startMin = timeToMinutes(slot.start_time)
  const endMin = timeToMinutes(slot.end_time)
  const topPx = minutesToPx(startMin)
  const heightPx = ((endMin - startMin) / 60) * PX_PER_HOUR
  const bg = courseColor(slot.course_code)
  const prof = lastName(slot.professor_name)

  return (
    <div
      className={[
        'absolute inset-x-0.5 rounded-md px-2 py-1 overflow-hidden',
        hasConflict ? 'opacity-50 ring-2 ring-njit-red' : '',
      ]
        .join(' ')
        .trim()}
      style={{ top: topPx, height: heightPx, backgroundColor: bg, border: `1px solid ${bg}cc` }}
    >
      <div className="flex items-baseline justify-between gap-1">
        <div className="flex items-baseline gap-1.5 min-w-0">
          <p className="font-mono font-bold text-[15px] text-text truncate">{slot.course_code}</p>
          {slot.section_number && (
            <p className="font-mono text-[11px] text-muted flex-shrink-0">{slot.section_number}</p>
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
