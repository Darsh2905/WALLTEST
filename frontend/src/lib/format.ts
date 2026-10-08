export const fmtInt = (n: number | null | undefined) => (n == null ? '—' : n.toLocaleString('en-US'))
export const fmtPct = (x: number | null | undefined, d = 1) => (x == null ? '—' : `${(x * 100).toFixed(d)}%`)
export const fmtAcc = (x: number | null | undefined) => (x == null ? '—' : x.toFixed(3))
export const fmtBits = (x: number | null | undefined) => (x == null ? '—' : x < 0.0005 ? '0' : x.toFixed(3))

/** p-values span many orders of magnitude; log10 (when known) keeps underflowed values honest. */
export function fmtP(p: number | null | undefined, log10?: number | null): string {
  if (p == null) return '—'
  if (p === 0 && log10 != null) return `10^${log10.toFixed(0)}`
  if (p === 0) return '< 1e-300'
  if (p >= 0.001) return p.toFixed(p >= 0.1 ? 3 : 4)
  const e = Math.floor(Math.log10(p))
  return `${(p / 10 ** e).toFixed(1)}e${e}`
}
export const shortHash = (h: string | null | undefined, n = 8) => (h ? h.slice(0, n) : '—')
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(+d) ? iso : d.toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}
export const fmtClock = (iso: string) => iso.slice(11, 26).replace('Z', '')       // HH:MM:SS.ffffff, microseconds kept
export function cellLabel(t: { vector_memory_on: boolean; notes_table_on: boolean; cache_on: boolean }): string {
  const parts = [t.vector_memory_on ? 'V' : '·', t.notes_table_on ? 'N' : '·', t.cache_on ? 'C' : '·']
  return parts.join(' ')
}
export const cellName = (t: { vector_memory_on: boolean; notes_table_on: boolean; cache_on: boolean }) => {
  const on = [t.vector_memory_on && 'vector', t.notes_table_on && 'notes', t.cache_on && 'cache'].filter(Boolean)
  return on.length === 3 ? 'all channels on' : on.length === 0 ? 'all channels off' : `${on.join(' + ')} only`
}
export const cx = (...a: (string | false | null | undefined)[]) => a.filter(Boolean).join(' ')
