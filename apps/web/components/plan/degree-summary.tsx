import type { ParsedDegreeValidated } from '@/lib/api'

interface DegreeSummaryProps {
  parsed: ParsedDegreeValidated
}

function Stat({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="flex flex-col gap-0.5">
      <p className="text-xs text-muted uppercase tracking-wider">{label}</p>
      <p className="font-mono text-lg tabular-nums text-text">{value === null ? 'Unknown' : value}</p>
      <p className="text-xs text-faint">credits</p>
    </div>
  )
}

export function DegreeSummary({ parsed }: DegreeSummaryProps) {
  const {
    student_name,
    majors,
    minors,
    credits_completed,
    credits_required,
    credits_remaining,
  } = parsed

  const pct =
    credits_required !== null && credits_completed !== null && credits_required > 0
      ? Math.min(100, Math.round((credits_completed / credits_required) * 100))
      : null
  const semestersLeft = credits_remaining === null ? null : Math.ceil(credits_remaining / 15)

  return (
    <div className="rounded-xl border border-border bg-surface p-6 space-y-5">
      <p className="text-xs font-medium uppercase tracking-wider text-muted">Student</p>

      <div className="flex items-center gap-2 flex-wrap">
        <p className="text-xl font-semibold tracking-tight">{student_name || 'Student name unavailable'}</p>
      </div>

      {(majors.length > 0 || minors.length > 0) && (
        <div className="flex flex-wrap gap-1.5">
          {majors.map((m) => (
            <span
              key={m}
              className="font-mono text-xs px-2 py-0.5 rounded-md bg-surface-2 border border-border text-text"
            >
              {m}
            </span>
          ))}
          {minors.map((m) => (
            <span
              key={m}
              className="font-mono text-xs px-2 py-0.5 rounded-md bg-surface-2 border border-border text-muted"
            >
              {m} (minor)
            </span>
          ))}
        </div>
      )}

      {/* Progress bar — rounded-full is spec-specified for this element */}
      {pct !== null && (
        <div className="h-1.5 rounded-full bg-surface-2 overflow-hidden">
          <div
            className="h-full rounded-full bg-njit-red transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}

      <div className="grid grid-cols-3 gap-4">
        <Stat label="Completed" value={credits_completed} />
        <Stat label="Remaining" value={credits_remaining} />
        <Stat label="Required" value={credits_required} />
      </div>

      {semestersLeft !== null && semestersLeft > 0 && (
        <p className="text-xs text-muted">
          ~{semestersLeft} semester{semestersLeft !== 1 ? 's' : ''} remaining
        </p>
      )}
    </div>
  )
}
