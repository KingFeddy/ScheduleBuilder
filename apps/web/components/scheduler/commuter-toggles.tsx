'use client'

import { useSchedulerStore } from '@/store/scheduler'

function buildTimeOptions(): { value: string; label: string }[] {
  const opts: { value: string; label: string }[] = []
  for (let h = 7; h <= 22; h++) {
    for (const m of [0, 30]) {
      const value = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
      const hour12 = h > 12 ? h - 12 : h === 0 ? 12 : h
      const ampm = h < 12 ? 'AM' : 'PM'
      const label = `${hour12}:${String(m).padStart(2, '0')} ${ampm}`
      opts.push({ value, label })
    }
  }
  return opts
}

const TIME_OPTIONS = buildTimeOptions()

export function CommuterToggles() {
  const { commuterOptions, setCommuterOptions } = useSchedulerStore()
  const { compact_week, earliest_start, latest_end, minimize_gaps } = commuterOptions

  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs font-medium uppercase tracking-wider text-muted">Commuter Mode</p>

      {/* Class Hours */}
      <div className="flex flex-col gap-2">
        <p className="text-sm font-medium text-text">Class Hours</p>
        <div className="flex gap-2">
          <div className="flex flex-col gap-1 flex-1 min-w-0">
            <p className="text-xs text-muted">Not before</p>
            <select
              value={earliest_start}
              onChange={(e) => setCommuterOptions({ earliest_start: e.target.value })}
              className="flex-1 min-w-0 rounded-md border border-border bg-surface-2 text-xs text-text px-2 py-1.5 focus:outline-none focus:border-border-strong transition-colors duration-150"
            >
              {TIME_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1 flex-1 min-w-0">
            <p className="text-xs text-muted">Not after</p>
            <select
              value={latest_end}
              onChange={(e) => setCommuterOptions({ latest_end: e.target.value })}
              className="flex-1 min-w-0 rounded-md border border-border bg-surface-2 text-xs text-text px-2 py-1.5 focus:outline-none focus:border-border-strong transition-colors duration-150"
            >
              {TIME_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Compact Week toggle */}
      <div
        className="flex items-center justify-between cursor-pointer group"
        onClick={() => setCommuterOptions({ compact_week: !compact_week })}
      >
        <div>
          <p className="text-sm font-medium text-text">Compact Week</p>
          <p className="text-xs text-muted">Prefer schedules with fewer campus days</p>
        </div>
        <div
          className="relative flex-shrink-0 w-9 h-5 rounded-full border transition-colors duration-150"
          style={
            compact_week
              ? { background: 'var(--njit-red)', borderColor: 'var(--njit-red)' }
              : { background: 'var(--surface-2)', borderColor: 'var(--border)' }
          }
        >
          <span
            className="absolute top-0.5 w-4 h-4 rounded-full bg-text transition-transform duration-150"
            style={{ transform: compact_week ? 'translateX(16px)' : 'translateX(2px)' }}
          />
        </div>
      </div>

      {/* Minimize Gaps toggle */}
      <div
        className="flex items-center justify-between cursor-pointer group"
        onClick={() => setCommuterOptions({ minimize_gaps: !minimize_gaps })}
      >
        <div>
          <p className="text-sm font-medium text-text">Minimize Gaps</p>
          <p className="text-xs text-muted">Prefer back-to-back classes</p>
        </div>
        <div
          className="relative flex-shrink-0 w-9 h-5 rounded-full border transition-colors duration-150"
          style={
            minimize_gaps
              ? { background: 'var(--njit-red)', borderColor: 'var(--njit-red)' }
              : { background: 'var(--surface-2)', borderColor: 'var(--border)' }
          }
        >
          <span
            className="absolute top-0.5 w-4 h-4 rounded-full bg-text transition-transform duration-150"
            style={{ transform: minimize_gaps ? 'translateX(16px)' : 'translateX(2px)' }}
          />
        </div>
      </div>
    </div>
  )
}
