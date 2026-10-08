import { useEffect, useMemo, useRef, useState } from 'react'
import * as d3 from 'd3'
import { cx } from '../lib/format'

export interface Series { name: string; label: string; color: string; dash?: string; points: { x: number; y: number }[] }

/** Cumulative-correct chart. Thin 2px lines, direct end labels + legend (identity is never color alone: dash pattern too),
 *  recessive grid, crosshair + tooltip, and a table view. A dashed reference line shows the null expectation n/2. */
export function LineChart({ series, height = 260, xLabel = 'slots scored', yLabel = 'cumulative correct', reference = true, tableName = 'chart' }: {
  series: Series[]; height?: number; xLabel?: string; yLabel?: string; reference?: boolean; tableName?: string
}) {
  const wrap = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(640)
  const [hover, setHover] = useState<number | null>(null)
  const [table, setTable] = useState(false)
  useEffect(() => {
    const ro = new ResizeObserver((es) => setW(Math.max(280, Math.floor(es[0].contentRect.width))))
    if (wrap.current) ro.observe(wrap.current)
    return () => ro.disconnect()
  }, [])
  const m = { l: 44, r: 118, t: 12, b: 34 }
  const iw = w - m.l - m.r, ih = height - m.t - m.b
  const maxX = Math.max(10, ...series.map((s) => (s.points.length ? s.points[s.points.length - 1].x : 0)))
  const maxY = Math.max(5, ...series.flatMap((s) => s.points.map((p) => p.y)), maxX * 0.5)
  const x = useMemo(() => d3.scaleLinear([0, maxX], [0, iw]), [maxX, iw])
  const y = useMemo(() => d3.scaleLinear([0, maxY * 1.05], [ih, 0]).nice(), [maxY, ih])
  const line = d3.line<{ x: number; y: number }>().x((d) => x(d.x)).y((d) => y(d.y)).curve(d3.curveMonotoneX)
  const xt = x.ticks(Math.min(8, Math.floor(iw / 70))), yt = y.ticks(5)
  const nearest = (n: number) => Math.max(1, Math.min(maxX, Math.round(n)))
  const at = hover != null ? hover : null

  // end-label collision avoidance: push labels at least 14px apart
  const labels = series.filter((s) => s.points.length).map((s) => ({ s, y: y(s.points[s.points.length - 1].y) })).sort((a, b) => a.y - b.y)
  for (let i = 1; i < labels.length; i++) if (labels[i].y - labels[i - 1].y < 14) labels[i].y = labels[i - 1].y + 14

  return (
    <div ref={wrap} className="relative w-full" data-testid="line-chart">
      <div className="flex items-center justify-between px-1 pb-1">
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[0.85rem]" aria-label="legend">
          {series.map((s) => (
            <li key={s.name} className="flex items-center gap-1.5 text-ink-2">
              <svg width="26" height="8" aria-hidden><line x1="0" y1="4" x2="26" y2="4" stroke={s.color} strokeWidth="2.5" strokeDasharray={s.dash} strokeLinecap="round" /></svg>{s.label}
            </li>
          ))}
          {reference && <li className="flex items-center gap-1.5 text-ink-3"><svg width="26" height="8" aria-hidden><line x1="0" y1="4" x2="26" y2="4" stroke="var(--ink-3)" strokeWidth="1.2" strokeDasharray="3 3" /></svg>chance (n/2)</li>}
        </ul>
        <button className="btn btn-sm btn-ghost text-ink-2" onClick={() => setTable((t) => !t)}>{table ? 'Chart view' : 'Table view'}</button>
      </div>
      {table ? (
        <div className="overflow-auto border border-line rounded-lg" style={{ height }}>
          <table className="t num"><thead><tr><th>n</th>{series.map((s) => <th key={s.name} className="r">{s.label}</th>)}</tr></thead>
            <tbody>{Array.from({ length: maxX }, (_, i) => i + 1).filter((n) => n % Math.max(1, Math.ceil(maxX / 40)) === 0 || n === maxX).map((n) => (
              <tr key={n}><td>{n}</td>{series.map((s) => <td key={s.name} className="r">{s.points.find((p) => p.x === n)?.y ?? '—'}</td>)}</tr>))}</tbody></table>
        </div>
      ) : (
        <svg width={w} height={height} role="img" aria-label={`${tableName}: ${yLabel} by ${xLabel}`}
          onMouseMove={(e) => { const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect(); const px = e.clientX - r.left - m.l; setHover(px >= 0 && px <= iw ? nearest(x.invert(px)) : null) }}
          onMouseLeave={() => setHover(null)}>
          <g transform={`translate(${m.l},${m.t})`}>
            {yt.map((t) => <g key={t}><line x1={0} x2={iw} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth="1" /><text x={-8} y={y(t)} dy="0.32em" textAnchor="end" fontSize="11" fill="var(--ink-3)" className="num">{t}</text></g>)}
            {xt.map((t) => <text key={t} x={x(t)} y={ih + 18} textAnchor="middle" fontSize="11" fill="var(--ink-3)" className="num">{t}</text>)}
            <line x1={0} x2={iw} y1={ih} y2={ih} stroke="var(--line)" />
            <text x={iw / 2} y={ih + 31} textAnchor="middle" fontSize="11" fill="var(--ink-3)">{xLabel}</text>
            <text transform={`translate(-34,${ih / 2}) rotate(-90)`} textAnchor="middle" fontSize="11" fill="var(--ink-3)">{yLabel}</text>
            {reference && <line x1={x(0)} y1={y(0)} x2={x(maxX)} y2={y(maxX / 2)} stroke="var(--ink-3)" strokeWidth="1.2" strokeDasharray="3 3" />}
            {series.map((s) => s.points.length > 0 && <path key={s.name} d={line(s.points) ?? ''} fill="none" stroke={s.color} strokeWidth="2" strokeDasharray={s.dash} strokeLinecap="round" strokeLinejoin="round" />)}
            {labels.map(({ s, y: ly }) => <text key={s.name} x={iw + 8} y={ly} dy="0.32em" fontSize="12" fill="var(--ink)" fontWeight="500">{s.label}</text>)}
            {at != null && (
              <g pointerEvents="none">
                <line x1={x(at)} x2={x(at)} y1={0} y2={ih} stroke="var(--ink-3)" strokeWidth="1" />
                {series.map((s) => { const p = s.points.find((q) => q.x === at); return p ? <circle key={s.name} cx={x(p.x)} cy={y(p.y)} r="4" fill={s.color} stroke="var(--surface)" strokeWidth="2" /> : null })}
              </g>
            )}
          </g>
        </svg>
      )}
      {at != null && !table && (
        <div className={cx('absolute pointer-events-none card px-2.5 py-1.5 text-[0.82rem] num z-10')} style={{ left: Math.min(w - 170, m.l + x(at) + 12), top: 38 }}>
          <div className="font-medium mb-0.5">after {at} slots</div>
          {series.map((s) => { const p = s.points.find((q) => q.x === at); return <div key={s.name} className="flex justify-between gap-4"><span className="text-ink-2">{s.label}</span><span>{p ? `${p.y} (${((p.y / at) * 100).toFixed(0)}%)` : '—'}</span></div> })}
        </div>
      )}
    </div>
  )
}
