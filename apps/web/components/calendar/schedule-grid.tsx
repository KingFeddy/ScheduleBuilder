import type { ScheduleResult } from '@/lib/api'
import { CourseBlock, type RenderedMeeting } from './course-block'
import { AsyncBlock, isAsyncSection } from './async-block'
import { calendarHours, PX_PER_HOUR } from '@/lib/calendar-time'

const DAY_MAP: Record<string, number> = { U: 0, M: 1, T: 2, W: 3, R: 4, F: 5, S: 6 }
const DAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function hourLabel(h: number): string {
  return `${h % 12 || 12}:00`
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
  const daySlots: RenderedMeeting[][] = DAY_LABELS.map(() => [])
  for (const slot of result.sections) {
    for (const meeting of slot.meetings) {
      if (!meeting.days || !meeting.start_time || !meeting.end_time) continue
      const rendered: RenderedMeeting = {
        crn: slot.crn,
        course_code: slot.course_code,
        section_number: slot.section_number,
        topic: slot.topic,
        section_title: slot.section_title,
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
  const { startHour, endHour } = calendarHours(daySlots.flat())
  const gridHeight = (endHour - startHour) * PX_PER_HOUR
  const hourMarks = Array.from({ length: endHour - startHour + 1 }, (_, i) => startHour + i)

  return (
    <div className="flex-1 min-h-0 flex flex-col rounded-xl border border-border bg-surface overflow-hidden">
      {/* Day header — not sticky inside overflow:hidden, so pinned via flex-shrink-0 */}
      <div
        className="flex-shrink-0 grid border-b border-border bg-bg"
        style={{ gridTemplateColumns: `48px repeat(${DAY_LABELS.length}, 1fr)` }}
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

      {/* Top padding leaves room for the first label above the opening line. */}
      <div className="overflow-y-auto flex-1 pt-3">
        <div
          className="grid"
          style={{ gridTemplateColumns: `48px repeat(${DAY_LABELS.length}, 1fr)`, height: gridHeight }}
        >
          {/* Time label column */}
          <div className="relative border-r border-border" style={{ height: gridHeight }}>
            {hourMarks.map((h, i) => (
              <span
                key={h}
                className="absolute right-2 text-xs leading-4 font-mono text-muted whitespace-nowrap select-none"
                style={{ top: i * PX_PER_HOUR, transform: 'translateY(-50%)' }}
              >
                {hourLabel(h)}
              </span>
            ))}
          </div>

          {/* Seven day columns */}
          {daySlots.map((slots, colIdx) => (
            <div
              key={colIdx}
              role="group"
              aria-label={`${DAY_LABELS[colIdx]} classes`}
              className="relative border-r border-border last:border-r-0"
              style={{ height: gridHeight }}
            >
              {/* Hourly and half-hour grid lines */}
              {hourMarks.map((hour, i) => (
                <div key={i}>
                  <div
                    className="absolute w-full border-t border-border"
                    style={{ top: i * PX_PER_HOUR }}
                  />
                  {hour < endHour && <div
                    className="absolute w-full border-t border-border opacity-40"
                    style={{ top: i * PX_PER_HOUR + PX_PER_HOUR / 2 }}
                  />}
                </div>
              ))}

              {/* Course blocks */}
              {slots.map((slot, i) => (
                <CourseBlock key={`${slot.crn}-${slot.start_time}-${colIdx}-${i}`} slot={slot} startHour={startHour} />
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
