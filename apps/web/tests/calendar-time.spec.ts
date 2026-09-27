import { test, expect } from '@playwright/test'
import { calendarHours } from '../lib/calendar-time'

test('keeps the default calendar range and expands to include early or late meetings', () => {
  expect(calendarHours([])).toEqual({ startHour: 8, endHour: 23 })
  expect(calendarHours([{ start_time: '10:00:00', end_time: '22:05:00' }]))
    .toEqual({ startHour: 8, endHour: 23 })
  expect(calendarHours([
    { start_time: '07:30:00', end_time: '08:50:00' },
    { start_time: '10:00 PM', end_time: '11:30 PM' },
  ])).toEqual({ startHour: 7, endHour: 24 })
})
