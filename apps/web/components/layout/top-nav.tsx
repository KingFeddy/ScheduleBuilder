'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { CalendarDays, Map } from 'lucide-react'
import { SCHEDULER_TERM } from '@/lib/scheduler-term'

const NAV_ITEMS = [
  { label: 'Scheduler', href: '/scheduler', icon: CalendarDays },
  { label: 'Planner',   href: '/planner',   icon: Map },
] as const

export function TopNav() {
  const pathname = usePathname()

  return (
    <header className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-2 border-b border-border px-4 py-3 sm:h-14 sm:flex-nowrap sm:gap-x-8 sm:px-5 sm:py-0">
      {/* Logo */}
      <Link href="/" className="flex shrink-0 items-center gap-2">
        <span className="font-mono font-bold text-sm text-njit-red">NJIT</span>
        <span className="font-mono text-sm text-muted">Schedule</span>
      </Link>

      {/* Nav */}
      <nav aria-label="Main navigation" className="order-last flex w-full items-center gap-1 sm:order-none sm:h-full sm:w-auto">
        {NAV_ITEMS.map(({ label, href, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`)
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? 'page' : undefined}
              className={[
                'flex items-center gap-2 border-b-2 px-3 py-2 text-sm transition-colors duration-150 sm:h-full',
                active
                  ? 'border-njit-red text-text'
                  : 'border-transparent text-muted hover:text-text',
              ].join(' ')}
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              {label}
            </Link>
          )
        })}
      </nav>
      <div className="ml-auto flex shrink-0 items-center gap-2 sm:gap-3">
        <span className="hidden text-xs text-muted sm:inline">Current semester</span>
        <span className="rounded-md border border-border bg-surface px-2.5 py-1 font-mono text-xs text-text">
          {SCHEDULER_TERM.label}
        </span>
      </div>
    </header>
  )
}
