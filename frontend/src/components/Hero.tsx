import { useMemo } from 'react'
import { AGENT_COLOR, AGENT_DASH } from '../lib/api'
import { Gate, Pulse, RunState } from '../lib/run'
import { fmtPct } from '../lib/format'

/** The wall: HIGH side left, the wall in the middle, LOW side right. The three shared channels are gates in the wall; a dot
 *  crosses a gate whenever a real access_event for that channel arrives from the database (writes by the research agent,
 *  reads by trading agents). Switched-off channels are hatched; blocked attempts stop at the gate. */
const GATES: { id: Gate; label: string[]; sub: string; y: number; flag: 'vector_memory' | 'notes_table' | 'cache' }[] = [
  { id: 'vector', label: ['VECTOR', 'MEMORY'], sub: 'pgvector', y: 62, flag: 'vector_memory' },
  { id: 'notes', label: ['NOTES', 'TABLE'], sub: 'agent_note', y: 162, flag: 'notes_table' },
  { id: 'cache', label: ['CACHE'], sub: 'cache', y: 262, flag: 'cache' },
]
const TRADERS = [
  { name: 'trader-leaky', y: 74 }, { name: 'trader-partial', y: 170 }, { name: 'trader-clean', y: 266 },
]
const WALL = { x: 452, w: 96 }
const GATE = { x: 462, w: 76, h: 84 }
const RESEARCH = { x: 232, y: 200 }

function pulsePath(p: Pulse): string {
  const g = GATES.find((q) => q.id === p.gate)!
  const gy = g.y + GATE.h / 2
  if (p.kind === 'write') {
    const ex = p.denied ? WALL.x - 6 : GATE.x + GATE.w
    return `M${RESEARCH.x + 30},${RESEARCH.y} C 340,${RESEARCH.y} 380,${gy} ${ex < WALL.x ? ex : GATE.x + 8},${gy}`
  }
  const t = TRADERS.find((q) => q.name === p.agent) ?? TRADERS[0]
  const ty = t.y + 33
  if (p.denied) return `M690,${ty} C 640,${ty} 600,${gy} ${GATE.x + GATE.w + 8},${gy}`
  return `M${GATE.x + GATE.w - 6},${gy} C 600,${gy} 640,${ty} 690,${ty}`
}

export default function Hero({ state, grants }: { state: RunState; grants: any[] | null }) {
  const flags = state.openSlot?.channels ?? null
  const granted = useMemo(() => {
    const set = new Set<string>()
    for (const g of grants ?? []) if (g.privilege === 'READ') set.add(`${g.agent_name}|${g.asset_name}`)
    return set
  }, [grants])
  const assetOf: Record<Gate, string> = { vector: 'vector_memory', notes: 'notes_table', cache: 'feature_cache' }
  const running = !!state.campaign && !state.finished

  return (
    <svg viewBox="0 0 1000 400" className="w-full block" style={{ aspectRatio: '1000 / 400', maxHeight: 420 }} role="img"
      aria-label="Information wall: research agent on the HIGH side, three shared channels as gates in the wall, trading agents on the LOW side" data-testid="hero">
      <defs>
        <pattern id="bricks" width="32" height="16" patternUnits="userSpaceOnUse">
          <rect width="32" height="16" fill="var(--surface-3)" />
          <path d="M0 .5H32M0 8.5H32M.5 0V8M16.5 8V16" stroke="var(--line)" strokeWidth="1" fill="none" />
        </pattern>
        <pattern id="hatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="7" height="7" fill="var(--surface-2)" /><line x1="0" y1="0" x2="0" y2="7" stroke="var(--ink-3)" strokeWidth="2" opacity=".55" />
        </pattern>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3.5" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
      </defs>

      {/* HIGH side */}
      <rect x="14" y="14" width="300" height="372" rx="12" fill="var(--surface-2)" stroke="var(--line)" />
      <text x="32" y="42" className="eyebrow" fontSize="11" fontWeight="600" letterSpacing="1.4" fill="var(--ink-3)">HIGH SIDE · INSIDE AREA</text>
      <g transform="translate(32,64)">
        <rect width="130" height="64" rx="8" fill="var(--surface)" stroke="var(--line)" />
        <g transform="translate(10,10)" stroke="var(--ink-2)" strokeWidth="1.6" fill="none" strokeLinecap="round"><path d="M3 8V5a4 4 0 0 1 8 0v3M1 8h12v9H1z" /></g>
        <text x="34" y="24" fontSize="12" fontWeight="600" fill="var(--ink)">UPSI + canary</text>
        <text x="34" y="40" fontSize="10.5" className="mono" fill="var(--ink-3)">upsi_item</text>
        <text x="34" y="53" fontSize="10.5" className="mono" fill="var(--ink-3)">canary_variant</text>
      </g>
      <path d="M162,98 C 190,98 200,200 202,200" stroke="var(--line)" strokeWidth="1.5" fill="none" strokeDasharray="3 3" />
      <circle cx={RESEARCH.x} cy={RESEARCH.y} r="30" fill="var(--surface)" stroke="var(--ink)" strokeWidth="1.8" />
      <path d={`M${RESEARCH.x - 11},${RESEARCH.y + 9} a11 11 0 0 1 22 0 M${RESEARCH.x},${RESEARCH.y - 2} m-6,-7 a6,6 0 1 0 12,0 a6,6 0 1 0 -12,0`} stroke="var(--ink)" strokeWidth="1.8" fill="none" strokeLinecap="round" />
      <text x={RESEARCH.x} y={RESEARCH.y + 52} textAnchor="middle" fontSize="12.5" fontWeight="600" fill="var(--ink)">research-agent</text>
      <text x={RESEARCH.x} y={RESEARCH.y + 67} textAnchor="middle" fontSize="10.5" fill="var(--ink-3)">writes a paraphrased note</text>
      <g transform="translate(32,300)">
        <rect width="262" height="68" rx="8" fill="var(--surface)" stroke="var(--line)" />
        <text x="12" y="20" fontSize="10.5" fontWeight="600" letterSpacing="1" fill="var(--ink-3)">THIS SLOT</text>
        {state.openSlot ? (<>
          <text x="12" y="39" fontSize="13" fontWeight="600" fill="var(--ink)" className="num">slot #{state.openSlot.index + 1} · canary shown: <tspan fill="var(--ink-3)">▮▮▮ sealed</tspan></text>
          <text x="12" y="57" fontSize="11" fill="var(--ink-2)">channels on: {[flags?.vector_memory && 'vector', flags?.notes_table && 'notes', flags?.cache && 'cache'].filter(Boolean).join(' · ') || 'none'}</text>
        </>) : (<text x="12" y="44" fontSize="12" fill="var(--ink-3)">{running ? 'waiting for the next slot…' : 'no audit running'}</text>)}
      </g>

      {/* grants: who may read which channel (access_grant), faint */}
      {GATES.map((g) => TRADERS.map((t) => granted.has(`${t.name}|${assetOf[g.id]}`) && (
        <path key={g.id + t.name} d={`M${GATE.x + GATE.w},${g.y + GATE.h / 2} C 600,${g.y + GATE.h / 2} 640,${t.y + 33} 690,${t.y + 33}`} fill="none" stroke="var(--line)" strokeWidth="1.2" />
      )))}
      {GATES.map((g) => <path key={'w' + g.id} d={`M${RESEARCH.x + 30},${RESEARCH.y} C 340,${RESEARCH.y} 380,${g.y + GATE.h / 2} ${GATE.x},${g.y + GATE.h / 2}`} fill="none" stroke="var(--line)" strokeWidth="1.2" />)}

      {/* the wall */}
      <rect x={WALL.x} y="14" width={WALL.w} height="372" fill="url(#bricks)" stroke="var(--ink-3)" strokeWidth="1.4" />
      <text x={WALL.x + WALL.w / 2} y="9" textAnchor="middle" fontSize="10.5" fontWeight="600" letterSpacing="1.6" fill="var(--ink-2)">INFORMATION WALL</text>
      {GATES.map((g) => {
        const off = flags ? !flags[g.flag] : false
        const tot = state.gateTotals[g.id]
        const active = state.pulses.some((p) => p.gate === g.id && !p.denied)
        return (
          <g key={g.id} data-gate={g.id} data-off={off ? '1' : '0'}>
            <rect x={GATE.x} y={g.y} width={GATE.w} height={GATE.h} rx="8" fill={off ? 'url(#hatch)' : 'var(--surface)'} stroke={active ? 'var(--steel)' : off ? 'var(--ink-3)' : 'var(--ink-2)'} strokeWidth={active ? 2.6 : 1.6} filter={active ? 'url(#glow)' : undefined} />
            {g.label.map((l, i) => <text key={l} x={GATE.x + GATE.w / 2} y={g.y + 28 + i * 13 - (g.label.length === 1 ? 6 : 0)} textAnchor="middle" fontSize="11" fontWeight="600" letterSpacing=".6" fill={off ? 'var(--ink-3)' : 'var(--ink)'}>{l}</text>)}
            <text x={GATE.x + GATE.w / 2} y={g.y + 58} textAnchor="middle" fontSize="9.5" className="mono" fill="var(--ink-3)">{g.sub}</text>
            {off && <text x={GATE.x + GATE.w / 2} y={g.y + 74} textAnchor="middle" fontSize="10" fontWeight="700" letterSpacing="1" fill="var(--ink)">OFF</text>}
            {!off && <text x={GATE.x + GATE.w / 2} y={g.y + 74} textAnchor="middle" fontSize="10" className="num" fill="var(--ink-2)">{tot.allowed} ok{tot.denied ? ` · ${tot.denied} ✕` : ''}</text>}
          </g>
        )
      })}

      {/* LOW side */}
      <rect x="686" y="14" width="300" height="372" rx="12" fill="var(--surface-2)" stroke="var(--line)" />
      <text x="704" y="42" fontSize="11" fontWeight="600" letterSpacing="1.4" fill="var(--ink-3)">LOW SIDE · PUBLIC AREA</text>
      {TRADERS.map((t) => {
        const a = state.agents[t.name]
        const present = !state.campaign || t.name in (state.agents || {})
        return (
          <g key={t.name} transform={`translate(700,${t.y + 0})`} opacity={present ? 1 : 0.35} data-agent={t.name}>
            <rect width="272" height="66" rx="8" fill="var(--surface)" stroke="var(--line)" />
            <rect x="0" y="0" width="5" height="66" rx="2.5" fill={AGENT_COLOR[t.name]} />
            <line x1="16" x2="42" y1="19" y2="19" stroke={AGENT_COLOR[t.name]} strokeWidth="2.5" strokeDasharray={AGENT_DASH[t.name]} strokeLinecap="round" />
            <text x="50" y="23" fontSize="13" fontWeight="600" fill="var(--ink)">{t.name}</text>
            <text x="16" y="42" fontSize="10.5" fill="var(--ink-3)">{t.name === 'trader-clean' ? 'price momentum only · no channel' : t.name === 'trader-partial' ? 'reads one shared channel' : 'reads all three shared channels'}</text>
            <text x="16" y="58" fontSize="12" className="num" fill="var(--ink-2)">{a && a.n > 0 ? `${a.correct}/${a.n} correct${running ? ' so far' : ''} · ${fmtPct(a.correct / a.n, 0)}` : 'orders scored after each slot'}</text>
          </g>
        )
      })}
      <g transform="translate(700,342)">
        <rect width="272" height="32" rx="8" fill="var(--surface)" stroke="var(--line)" strokeDasharray="4 3" />
        <text x="136" y="20" textAnchor="middle" fontSize="11" fill="var(--ink-2)"><tspan className="mono">daily_price</tspan> · real NSE end-of-day · public</text>
      </g>

      {/* pulses: real access_events crossing the gates */}
      {state.pulses.map((p) => (
        <g key={p.id} pointerEvents="none" data-pulse={p.gate} data-denied={p.denied ? '1' : '0'}>
          <circle r={p.denied ? 4 : 5} fill={p.denied ? 'var(--ink-3)' : 'var(--steel)'} filter={p.denied ? undefined : 'url(#glow)'}>
            <animateMotion dur="0.85s" path={pulsePath(p)} fill="freeze" keyPoints="0;1" keyTimes="0;1" calcMode="linear" />
            <animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.12;.85;1" dur="1.1s" fill="freeze" />
          </circle>
        </g>
      ))}
    </svg>
  )
}
