export const MDASH = '\u2014'

export function fmtMaybeNumber(v, decimals = 2) {
  if (v === null || v === undefined) {
    return MDASH
  }
  const n = Number(v)
  if (Number.isNaN(n)) {
    return MDASH
  }

  return n.toFixed(decimals)
}
