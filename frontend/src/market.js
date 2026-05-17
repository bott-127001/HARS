export function isMarketHoursIST(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(now)

  const map = Object.fromEntries(parts.filter((p) => p.type !== 'literal').map((p) => [p.type, p.value]))
  const weekday = map.weekday
  if (weekday === 'Sat' || weekday === 'Sun') {
    return false
  }

  const hour = Number(map.hour)
  const minute = Number(map.minute)
  const t = hour * 60 + minute
  const open = 9 * 60 + 15
  const close = 15 * 60 + 30
  return t >= open && t <= close
}
