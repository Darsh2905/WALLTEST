import { useEffect, useMemo, useRef, useState } from 'react'
import * as d3 from 'd3'
import { Chip, EmptyState, PageHeader, Panel, Skeleton } from '../components/ui'
import { useApi } from '../lib/api'
import { cx, fmtInt } from '../lib/format'

interface Col { name: string; type: string; pk: boolean; unique: boolean; fk: boolean; not_null: boolean; identity: boolean }
interface Tbl { name: string; columns: Col[]; rows: number; rls: boolean; policies: number; triggers: string[]; checks: number; exclusions: number }
interface Fk { conname: string; from_table: string; to_table: string; from_cols: string[]; to_cols: string[] }
interface SchemaData { tables: Tbl[]; fks: Fk[]; groups: Record<string, string[]>; group_of: Record<string, string> }

const GROUP_COLOR: Record<string, string> = {
  'Organisation, walls and access': 'var(--steel)', 'Market and confidential information': 'var(--s-clean)',
  'Canary audit': 'var(--s-partial)', 'Behaviour log (append-only)': 'var(--ink-2)',
}
const COLS_PER_GROUP: Record<string, number> = { 'Organisation, walls and access': 3, 'Market and confidential information': 2, 'Canary audit': 3, 'Behaviour log (append-only)': 3 }
const ROW_H = 15, HEAD_H = 40

function layout(data: SchemaData, detailed: boolean) {
  const W = detailed ? 250 : 168, GAP = 18, PAD = 18, TOP = 36
  const tmap = new Map(data.tables.map((t) => [t.name, t]))
  const boxes = new Map<string, { x: number; y: number; w: number; h: number; group: string }>()
  const groups: { name: string; x: number; y: number; w: number; h: number }[] = []
  const place = (gname: string, ox: number, oy: number) => {
    const names = data.groups[gname].filter((n) => tmap.has(n))
    const nc = COLS_PER_GROUP[gname]
    const heights = names.map((n) => HEAD_H + tmap.get(n)!.columns.length * ROW_H + 8)
    const colY = Array(nc).fill(TOP + PAD / 2)
    names.forEach((n, i) => {
      let c = 0
      for (let k = 1; k < nc; k++) if (colY[k] < colY[c]) c = k
      boxes.set(n, { x: ox + PAD + c * (W + GAP), y: oy + colY[c], w: W, h: heights[i], group: gname })
      colY[c] += heights[i] + GAP
    })
    const w = PAD * 2 + nc * W + (nc - 1) * GAP
    const h = Math.max(...colY) - GAP + PAD
    groups.push({ name: gname, x: ox, y: oy, w, h })
    return { w, h }
  }
  const names = Object.keys(data.groups)
  const A = place(names[0], 0, 0)
  const B = place(names[1], A.w + 30, 0)
  const top = Math.max(A.h, B.h) + 30
  const C = place(names[2], 0, top)
  const D = place(names[3], C.w + 30, top)
  return { boxes, groups, width: Math.max(A.w + 30 + B.w, C.w + 30 + D.w), height: top + Math.max(C.h, D.h) }
}

function edgePath(a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }, fy: number, ty: number) {
  const ac = a.x + a.w / 2, bc = b.x + b.w / 2
  if (Math.abs(ac - bc) > (a.w + b.w) / 2 - 2) {
    const right = bc > ac
    const x1 = right ? a.x + a.w : a.x, x2 = right ? b.x : b.x + b.w
    const dx = Math.max(24, Math.abs(x2 - x1) / 2)
    return { d: `M${x1},${fy} C ${x1 + (right ? dx : -dx)},${fy} ${x2 + (right ? -dx : dx)},${ty} ${x2},${ty}`, x1, x2, right }
  }
  const below = b.y > a.y
  const x1 = a.x + a.w / 2 + 18, x2 = b.x + b.w / 2 - 18
  const y1 = below ? a.y + a.h : a.y, y2 = below ? b.y : b.y + b.h
  const dy = Math.max(24, Math.abs(y2 - y1) / 2)
  return { d: `M${x1},${y1} C ${x1},${y1 + (below ? dy : -dy)} ${x2},${y2 + (below ? -dy : dy)} ${x2},${y2}`, x1, x2, right: true }
}

export default function Schema() {
  const s = useApi<SchemaData>('/api/schema')
  const [detailed, setDetailed] = useState(false)
  const [sel, setSel] = useState<string | null>(null)
  const svg = useRef<SVGSVGElement>(null)
  const gRef = useRef<SVGGElement>(null)
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null)
  const L = useMemo(() => (s.data ? layout(s.data, detailed) : null), [s.data, detailed])
  const tmap = useMemo(() => new Map((s.data?.tables ?? []).map((t) => [t.name, t])), [s.data])

  const fit = (target?: { x: number; y: number; w: number; h: number }, tries = 0) => {
    if (!svg.current || !L || !zoomRef.current) return
    const r = svg.current.getBoundingClientRect()
    if ((r.width < 50 || r.height < 50) && tries < 20) { requestAnimationFrame(() => fit(target, tries + 1)); return }
    const b = target ?? { x: 0, y: 0, w: L.width, h: L.height }
    const k = Math.min(r.width / (b.w + 40), r.height / (b.h + 40), 1.6)
    const t = d3.zoomIdentity.translate((r.width - b.w * k) / 2 - b.x * k, (r.height - b.h * k) / 2 - b.y * k).scale(k)
    d3.select(svg.current).transition().duration(350).call(zoomRef.current.transform, t)
  }
  useEffect(() => {
    if (!svg.current || !gRef.current) return
    const z = d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.3, 3]).on('zoom', (e) => d3.select(gRef.current).attr('transform', e.transform.toString()))
    zoomRef.current = z
    d3.select(svg.current).call(z)
    return () => { d3.select(svg.current).on('.zoom', null) }
  }, [])
  useEffect(() => { if (L) requestAnimationFrame(() => fit()) }, [L]) // eslint-disable-line
  useEffect(() => { const ro = new ResizeObserver(() => fit()); if (svg.current) ro.observe(svg.current); return () => ro.disconnect() }, [L]) // eslint-disable-line

  const total = (s.data?.tables ?? []).reduce((n, t) => n + t.rows, 0)
  const selTable = sel ? tmap.get(sel) : null
  const related = useMemo(() => {
    const set = new Set<string>()
    if (sel) for (const f of s.data?.fks ?? []) { if (f.from_table === sel) set.add(f.to_table); if (f.to_table === sel) set.add(f.from_table) }
    return set
  }, [sel, s.data])

  return (
    <div>
      <PageHeader title="Schema" lead="The 20 relations of the proposal, introspected live from the PostgreSQL catalog (not drawn by hand): attributes with their key roles, foreign keys, and per-table row counts." />
      <div className="grid gap-5">
        <Panel title="ER diagram" sql={s.sql} loading={s.loading && !s.data} error={s.error} onRetry={s.reload}
          subtitle={s.data ? `${s.data.tables.length} tables · ${s.data.fks.length} foreign keys · ${fmtInt(total)} rows · drag to pan, scroll to zoom` : ''}
          actions={<div className="flex items-center gap-1.5">
            {s.data && Object.keys(s.data.groups).map((g) => <button key={g} className="btn btn-sm" onClick={() => L && fit(L.groups.find((x) => x.name === g))} title={g}><span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: GROUP_COLOR[g] }} />{g.split(',')[0].split(' ')[0]}</button>)}
            <button className="btn btn-sm" onClick={() => fit()}>Fit</button>
            <button className="btn btn-sm" aria-pressed={detailed} onClick={() => setDetailed((d) => !d)}>{detailed ? 'Compact' : 'Types'}</button></div>}>
          <div className="relative" style={{ height: 760 }} data-testid="er">
            {!L ? <div className="p-4"><Skeleton h={600} /></div> : (
              <svg ref={svg} className="w-full h-full cursor-grab active:cursor-grabbing select-none" role="img" aria-label="Entity-relationship diagram of the 20 WALLTEST tables">
                <defs>
                  <marker id="crow" viewBox="0 0 12 12" refX="1" refY="6" markerWidth="12" markerHeight="12" orient="auto-start-reverse"><path d="M11 1L2 6l9 5M2 6h9" fill="none" stroke="var(--ink-3)" strokeWidth="1.3" /></marker>
                  <marker id="one" viewBox="0 0 12 12" refX="9" refY="6" markerWidth="12" markerHeight="12" orient="auto"><path d="M9 1v10" stroke="var(--ink-3)" strokeWidth="1.6" /></marker>
                </defs>
                <g ref={gRef}>
                  {L.groups.map((g) => (
                    <g key={g.name}><rect x={g.x} y={g.y} width={g.w} height={g.h} rx="12" fill="var(--surface-2)" stroke={GROUP_COLOR[g.name]} strokeOpacity=".55" strokeWidth="1.4" />
                      <text x={g.x + 18} y={g.y + 24} fontSize="13" fontWeight="600" fill={GROUP_COLOR[g.name]}>{g.name}</text></g>))}
                  {s.data!.fks.map((f) => {
                    const a = L.boxes.get(f.from_table), b = L.boxes.get(f.to_table)
                    if (!a || !b || f.from_table === f.to_table) return null
                    const t = tmap.get(f.from_table)!, ci = Math.max(0, t.columns.findIndex((c) => c.name === f.from_cols[0]))
                    const tt = tmap.get(f.to_table)!, tj = Math.max(0, tt.columns.findIndex((c) => c.name === f.to_cols[0]))
                    const p = edgePath(a, b, a.y + HEAD_H + ci * ROW_H + ROW_H / 2, b.y + HEAD_H + tj * ROW_H + ROW_H / 2)
                    const on = sel && (f.from_table === sel || f.to_table === sel)
                    return <path key={f.conname} d={p.d} fill="none" stroke={on ? 'var(--ink)' : 'var(--ink-3)'} strokeWidth={on ? 1.8 : 1} opacity={sel ? (on ? 0.95 : 0.1) : 0.45} markerStart="url(#crow)" markerEnd="url(#one)" />
                  })}
                  {s.data!.tables.map((t) => {
                    const b = L.boxes.get(t.name)!
                    const dim = sel && sel !== t.name && !related.has(t.name)
                    const appendOnly = t.triggers.some((x) => x.includes('append_only'))
                    return (
                      <g key={t.name} transform={`translate(${b.x},${b.y})`} opacity={dim ? 0.35 : 1} onClick={(e) => { e.stopPropagation(); setSel(sel === t.name ? null : t.name) }} className="cursor-pointer" data-table={t.name}>
                        <rect width={b.w} height={b.h} rx="7" fill="var(--surface)" stroke={sel === t.name ? 'var(--ink)' : 'var(--line)'} strokeWidth={sel === t.name ? 2 : 1} />
                        <rect width={b.w} height={HEAD_H} rx="7" fill={GROUP_COLOR[b.group]} opacity=".14" /><rect y={HEAD_H - 7} width={b.w} height="7" fill={GROUP_COLOR[b.group]} opacity=".14" />
                        <text x="9" y="16" fontSize="12" fontWeight="600" fill="var(--ink)" className="mono">{t.name}</text>
                        <text x="9" y="31" fontSize="9.5" fill="var(--ink-2)" className="num">{fmtInt(t.rows)} rows{t.rls ? ' · RLS' : ''}{appendOnly ? ' · append-only' : ''}{t.exclusions ? ' · EXCLUDE' : ''}</text>
                        {t.columns.map((c, i) => (
                          <g key={c.name} transform={`translate(0,${HEAD_H + i * ROW_H})`}>
                            <text x="9" y="11" fontSize="9" fontWeight="700" fill={c.pk ? 'var(--ink)' : c.fk ? 'var(--steel)' : 'var(--ink-3)'} className="mono">{c.pk && c.fk ? 'PK,FK' : c.pk ? 'PK' : c.fk ? 'FK' : c.unique ? 'UK' : ''}</text>
                            <text x="40" y="11" fontSize="10.5" fill="var(--ink)" textDecoration={c.pk ? 'underline' : undefined}>{c.name}</text>
                            {detailed && <text x={b.w - 8} y="11" fontSize="9.5" textAnchor="end" fill="var(--ink-3)" className="mono">{c.type.replace('timestamp with time zone', 'timestamptz').replace('character varying', 'varchar').replace('double precision', 'float8')}</text>}
                          </g>))}
                      </g>)
                  })}
                </g>
                <rect width="100%" height="100%" fill="transparent" onClick={() => setSel(null)} style={{ pointerEvents: 'none' }} />
              </svg>)}
          </div>
        </Panel>

        <div className="grid gap-5 content-start" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 2fr)" }}>
          <Panel title="Row counts" sql={undefined} bodyClass="max-h-[420px] overflow-auto">
            {!s.data ? <div className="p-3"><Skeleton h={200} /></div> : <table className="t num" data-testid="row-counts"><tbody>
              {[...s.data.tables].sort((a, b) => b.rows - a.rows).map((t) => <tr key={t.name} className={cx('cursor-pointer', sel === t.name && 'bg-steel-bg')} onClick={() => setSel(sel === t.name ? null : t.name)} data-count-table={t.name}><td className="mono text-[0.82rem]">{t.name}</td><td className="r">{fmtInt(t.rows)}</td></tr>)}</tbody></table>}
          </Panel>
          <Panel title={selTable ? selTable.name : 'Table details'} bodyClass="p-3 text-[0.86rem]">
            {!selTable ? <EmptyState title="Select a table">Click a table in the diagram or the row-count list to see its triggers, policies and checks.</EmptyState> : (
              <div className="space-y-2">
                <div className="flex flex-wrap gap-1.5"><Chip>{fmtInt(selTable.rows)} rows</Chip>{selTable.rls && <Chip tone="steel">RLS · {selTable.policies} policies</Chip>}<Chip>{selTable.checks} CHECKs</Chip>{selTable.exclusions > 0 && <Chip tone="steel">EXCLUDE</Chip>}</div>
                <div className="eyebrow">triggers</div>
                {selTable.triggers.length ? <ul className="mono text-[0.78rem] space-y-0.5">{selTable.triggers.map((t) => <li key={t}>{t}</li>)}</ul> : <div className="text-ink-3">none</div>}
                <div className="eyebrow pt-1">columns</div>
                <ul className="mono text-[0.78rem] space-y-0.5">{selTable.columns.map((c) => <li key={c.name}><span className="text-ink-3">{c.pk ? 'PK ' : c.fk ? 'FK ' : c.unique ? 'UK ' : '   '}</span>{c.name} <span className="text-ink-3">{c.type}</span></li>)}</ul>
              </div>)}
          </Panel>
        </div>
      </div>
    </div>
  )
}
