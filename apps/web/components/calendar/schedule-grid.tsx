import type { ScheduleResult } from '@/lib/api'
import { CourseBlock, type RenderedMeeting } from './course-block'
import { AsyncBlock, isAsyncSection } from './async-block'

const PX_PER_HOUR = 48
const START_HOUR = 7
const END_HOUR = 22
const TOTAL_HOURS = END_HOUR - START_HOUR // 15
const GRID_HEIGHT = TOTAL_HOURS * PX_PER_HOUR // 720px — used for all position math
const GRID_DISPLAY_HEIGHT = GRID_HEIGHT + PX_PER_HOUR / 2 // 744px — extends to 10:30pm closing line

const DAY_MAP: Record<string, number> = { M: 0, T: 1, W: 2, R: 3, F: 4 }
const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']

// 16 hour labels: 7am through 10pm inclusive
const HOUR_MARKS = Array.from({ length: TOTAL_HOURS + 1 }, (_, i) => START_HOUR + i)
// 16 rows: 15 hour slots + closing row that draws the 10pm hourly line
const GRID_ROWS = Array.from({ length: TOTAL_HOURS + 1 }, (_, i) => i)

function hourLabel(h: number): string {
  if (h === 12) return '12:00'
  return h > 12 ? `${h - 12}:00` : `${h}:00`
}

interface ScheduleGridProps {
  result: ScheduleResult | null
}

export function ScheduleGrid({ result }: ScheduleGridProps) {
  if (!result) {
    return (
      <div className="flex-1 flex items-center justify-center rounded-xl border border-border bg-surface">
        <p className="text-sm text-muted">Click Solve to generate schedules</p>
      </div>
    )
  }

  // Distribute one RenderedMeeting per (section, meeting, day) combination
  // into per-day buckets. A section with multiple meeting rows (e.g. a
  // Monday-only row and a separate Thursday-only row at the same CRN)
  // produces a separate RenderedMeeting per row, so both actually render
  // instead of only the first one Banner happened to list.
  const daySlots: RenderedMeeting[][] = [[], [], [], [], []]
  for (const slot of result.sections) {
    for (const meeting of slot.meetings) {
      if (!meeting.days || !meeting.start_time || !meeting.end_time) continue
      const rendered: RenderedMeeting = {
        crn: slot.crn,
        course_code: slot.course_code,
        section_number: slot.section_number,
        professor_name: slot.professor_name,
        open_seats: slot.open_seats,
        total_seats: slot.total_seats,
        start_time: meeting.start_time,
        end_time: meeting.end_time,
        location: meeting.location,
      }
      for (const ch of meeting.days) {
        const idx = DAY_MAP[ch]
        if (idx != null) daySlots[idx].push(rendered)
      }
    }
  }

  const asyncSlots = result.sections.filter(isAsyncSection)

  return (
    <div className="flex-1 min-h-0 flex flex-col rounded-xl border border-border bg-surface overflow-hidden">
      {/* Day header — not sticky inside overflow:hidden, so pinned via flex-shrink-0 */}
      <div
        className="flex-shrink-0 grid border-b border-border bg-bg"
        style={{ gridTemplateColumns: '48px repeat(5, 1fr)' }}
      >
        <div />
        {DAY_LABELS.map((day) => (
          <div
            key={day}
            className="flex items-center justify-center py-1.5 text-xs font-medium uppercase tracking-wider text-muted"
          >
            {day}
          </div>
        ))}
      </div>

      {/* Scrollable body — pt-3 gives the 7:00 label room above y=0 so translateY(-50%) doesn't clip */}
      <div className="overflow-y-auto flex-1 pt-3">
        <div
          className="grid"
          style={{ gridTemplateColumns: '48px repeat(5, 1fr)', height: GRID_DISPLAY_HEIGHT }}
        >
          {/* Time label column */}
          <div className="relative border-r border-border" style={{ height: GRID_DISPLAY_HEIGHT }}>
            {HOUR_MARKS.map((h, i) => (
              <span
                key={h}
                className="absolute right-2 text-[10px] font-mono text-faint select-none"
                style={{ top: i * PX_PER_HOUR, transform: 'translateY(-50%)' }}
              >
                {hourLabel(h)}
              </span>
            ))}
          </div>

          {/* Five day columns */}
          {daySlots.map((slots, colIdx) => (
            <div
              key={colIdx}
              className="relative border-r border-border last:border-r-0"
              style={{ height: GRID_DISPLAY_HEIGHT }}
            >
              {/* Hourly and half-hour grid lines */}
              {GRID_ROWS.map((i) => (
                <div key={i}>
                  <div
                    className="absolute w-full border-t border-border"
                    style={{ top: i * PX_PER_HOUR }}
                  />
                  <div
                    className="absolute w-full border-t border-border opacity-40"
                    style={{ top: i * PX_PER_HOUR + PX_PER_HOUR / 2 }}
                  />
                </div>
              ))}

              {/* Course blocks */}
              {slots.map((slot) => (
                <CourseBlock key={`${slot.crn}-${slot.start_time}-${colIdx}`} slot={slot} />
              ))}
            </div>
          ))}
        </div>

        {/* Async/TBA row — sections with no scheduled meeting time have
            nowhere on the day grid to render, so they get their own row
            here instead of silently disappearing. The label+divider always
            shows, even with none this schedule, so the section is a
            permanent, predictable part of the layout. */}
        <div className="px-3">
          <div className="flex items-center gap-2 pt-3 pb-1.5">
            <p className="text-xs font-medium uppercase tracking-wider text-muted whitespace-nowrap">
              Async / TBA
            </p>
            <div className="flex-1 h-px bg-border" />
          </div>
          {asyncSlots.length > 0 && (
            <div className="flex flex-wrap gap-2 pb-3">
              {asyncSlots.map((slot) => (
                <AsyncBlock key={slot.crn} slot={slot} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
