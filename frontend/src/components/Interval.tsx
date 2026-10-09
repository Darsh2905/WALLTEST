/** A two-sided interval on the accuracy scale [0.3, 1]: lower bound, point estimate, upper bound, with chance (0.5) and an
 *  optional materiality line. v1 drew only the lower bound; the upper bound is what makes "NO EVIDENCE" informative
 *  ("whatever leak there is, accuracy is below acc_upper"). */
export function Interval({ lo, acc, hi, width = 132, materiality }: { lo: number | null; acc: number | null; hi: number | null; width?: number; materiality?: number }) {
  if (acc == null) return <span className="text-ink-3">—</span>
  const x = (v: number) => 4 + ((Math.min(1, Math.max(0.3, v)) - 0.3) / 0.7) * (width - 8)
  const l = lo ?? 0.3, h = hi ?? 1
  return (
    <svg width={width} height="18" role="img" aria-label={`accuracy ${acc.toFixed(3)}, interval ${l.toFixed(3)} to ${h.toFixed(3)}`}>
      <line x1={x(0.5)} x2={x(0.5)} y1="1" y2="17" stroke="var(--ink-3)" strokeDasharray="2 2" />
      {materiality != null && <line x1={x(materiality)} x2={x(materiality)} y1="3" y2="15" stroke="var(--leak)" strokeOpacity="0.45" strokeDasharray="1 2" />}
      <line x1={x(l)} x2={x(h)} y1="9" y2="9" stroke="var(--ink-2)" strokeWidth="2" strokeLinecap="round" />
      <line x1={x(l)} x2={x(l)} y1="5" y2="13" stroke="var(--ink-2)" strokeWidth="2" />
      <line x1={x(h)} x2={x(h)} y1="5" y2="13" stroke="var(--ink-2)" strokeWidth="2" />
      <circle cx={x(acc)} cy="9" r="4" fill="var(--ink)" stroke="var(--surface)" strokeWidth="2" />
    </svg>
  )
}
