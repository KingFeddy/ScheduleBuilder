// Spec §5 SeatStatus: green if >3 open, yellow if ≤3, red if 0.
// Single source of truth — both CourseBlock (day-grid) and AsyncBlock
// import seatColorClass so the two can never show contradicting colors for
// the same section.
export function seatColorClass(open: number): string {
  if (open === 0) return 'text-njit-red'
  if (open <= 3) return 'text-yellow'
  return 'text-green'
}

export function SeatStatus({ open, total }: { open: number; total: number }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={`text-xs ${seatColorClass(open)}`}>●</span>
      <span className="font-mono tabular-nums text-xs text-text">
        {open} / {total}
      </span>
    </span>
  )
}
