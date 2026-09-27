export const PX_PER_HOUR = 48

export function timeToMinutes(time: string): number {
  const ampm = /(\d+):(\d+)\s*(AM|PM)/i.exec(time)
  if (ampm) {
    const hour = Number(ampm[1]) % 12 + (ampm[3].toUpperCase() === 'PM' ? 12 : 0)
    return hour * 60 + Number(ampm[2])
  }
  const [hour, minute] = time.split(':').map(Number)
  return hour * 60 + minute
}

export function calendarHours(meetings: readonly { start_time: string; end_time: string }[]) {
  let startHour = 8
  let endHour = 23
  for (const meeting of meetings) {
    startHour = Math.min(startHour, Math.floor(timeToMinutes(meeting.start_time) / 60))
    endHour = Math.max(endHour, Math.ceil(timeToMinutes(meeting.end_time) / 60))
  }
  return { startHour, endHour }
}
