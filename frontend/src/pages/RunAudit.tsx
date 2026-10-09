import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { LineChart, Series } from '../components/LineChart'
import { Banner, Chip, EmptyState, IconLock, IconPlay, IconStop, PageHeader, Panel, Stat, VerdictChip } from '../components/ui'
import { AGENT_COLOR, AGENT_DASH, VerdictsData, useApi, useSql } from '../lib/api'
import { useRun } from '../lib/run'
import { cx, fmtAcc, fmtClock, fmtInt, fmtPct } from '../lib/format'

type Mode = 'vary' | 'on' | 'off'
interface Form {
  alpha: number; design: string; planned_slots: number; clock_mode: 'LIVE' | 'SIMULATED'; slot_ms: number; null_control: boolean
  trustLeaky: number; trustPartial: number; llm: boolean; partial_channel: string; channels: Record<string, Mode>; seed: string
  inference: 'FIXED' | 'SEQUENTIAL'; stop_rule: 'MAX' | 'FIRST_LEAK' | 'ALL_SETTLED'; materiality: number
}
const CH = ['vector_memory', 'notes_table', 'cache'] as const

function cellsOf(f: Form): number {
  if (f.design === 'FULL_FACTORIAL') return 8
  if (f.design === 'ONE_AT_A_TIME') return 5
  if (f.design === 'ALL_ON') return 1
  return CH.reduce((n, c) => n * (f.channels[c] === 'vary' ? 2 : 1), 1)
}

export default function RunAudit() {
  const { state, start, cancel } = useRun()
  const defaults = useApi<any>('/api/defaults')
  const meta = useApi<any>('/api/meta')
  const [form, setForm] = useState<Form | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const f: Form | null = form ?? (defaults.data ? fromPreset(defaults.data.live) : null)
  const set = (patch: Partial<Form>) => setForm({ ...(f as Form), ...patch })

  function fromPreset(p: any): Form {
    return { alpha: p.alpha, design: p.design, planned_slots: p.planned_slots, clock_mode: p.clock_mode, slot_ms: p.slot_ms ?? 1000, null_control: !!p.null_control,
             trustLeaky: p.trust?.['trader-leaky'] ?? 0.9, trustPartial: p.trust?.['trader-partial'] ?? 0.9, llm: false, partial_channel: p.partial_channel ?? 'vector_memory',
             channels: { vector_memory: 'vary', notes_table: 'vary', cache: 'vary' }, seed: '',
             inference: p.inference ?? 'FIXED', stop_rule: p.stop_rule ?? 'MAX', materiality: p.materiality ?? 0.65 }
  }
  const cells = f ? cellsOf(f) : 1
  const nLow = f?.null_control ? 1 : f?.llm ? 2 : 3
  const K = cells * nLow
  const perCell = f ? f.planned_slots / cells : 0
  const valid = !!f && Number.isInteger(perCell) && perCell >= 2 && f.planned_slots <= (f.clock_mode === 'SIMULATED' ? 3500 : 1000)
  const plantedAcc = f ? 0.5 + Math.max(f.trustLeaky, f.trustPartial) / 2 : 0.95
  const power = useApi<any>(f && valid ? `/api/power/point?n=${perCell}&alpha=${f.alpha / K}&acc=${f.null_control ? 0.55 : plantedAcc}&power=0.8` : null, [f?.alpha, K, perCell, plantedAcc])
  // v2: the claim that matters first is each agent's pooled gate test, at alpha / n_agents over ALL planned slots
  const gatePower = useApi<any>(f && valid ? `/api/power/point?n=${f.planned_slots}&alpha=${f.alpha / nLow}&acc=${f.null_control ? 0.55 : plantedAcc}&power=0.8` : null, [f?.alpha, nLow, f?.planned_slots, plantedAcc])
  const running = !!state.campaign && !state.finished
  const sqlLive = useSql(['progress_series', 'access_summary', 'verdicts_live', 'peeking_trajectory'])
  const eta = f ? (f.clock_mode === 'LIVE' ? (f.planned_slots * f.slot_ms) / 1000 + 4 : f.planned_slots / 50) : 0

  async function go() {
    if (!f) return
    setBusy(true); setErr(null)
    try {
      const body: any = { alpha: f.alpha, planned_slots: f.planned_slots, design: f.design, clock_mode: f.clock_mode, slot_ms: f.slot_ms,
        null_control: f.null_control, llm: f.llm, trust: { 'trader-leaky': f.trustLeaky, 'trader-partial': f.trustPartial }, partial_channel: f.partial_channel,
        channels: f.channels, seed: f.seed ? Number(f.seed) : null, inference: f.inference,
        stop_rule: f.inference === 'SEQUENTIAL' ? f.stop_rule : 'MAX', materiality: f.materiality }
      await start(body)
    } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }

  const series: Series[] = useMemo(() => Object.entries(state.series).sort().map(([name, arr]) => ({
    name, label: name.replace('trader-', ''), color: AGENT_COLOR[name], dash: AGENT_DASH[name],
    points: arr.map((y, i) => ({ x: i + 1, y })).filter((p) => p.y != null),
  })), [state.series])
  const evSeries: Series[] = useMemo(() => Object.entries(state.evalues).sort().map(([name, pts]) => ({
    name, label: name.replace('trader-', ''), color: AGENT_COLOR[name], dash: AGENT_DASH[name], points: pts })), [state.evalues])
  const sequentialRun = state.campaign?.inference === 'SEQUENTIAL'
  const evMax = Math.max(state.evThreshold ?? 3, ...evSeries.flatMap((s) => s.points.map((p) => p.y)))
  const evMin = Math.min(-1, ...evSeries.flatMap((s) => s.points.map((p) => p.y)))
  const lastCheck = state.checks[state.checks.length - 1]
  const pct = state.campaign ? state.slotsDone / state.campaign.planned_slots : 0
  const frozen = useApi<VerdictsData>(state.frozen && state.campaign ? `/api/campaigns/${state.campaign.campaign_id}/verdicts` : null, [state.frozen])

  const presets = defaults.data ? [
    { id: 'live', label: 'Live demo', sub: `${defaults.data.live.planned_slots} slots · α ${defaults.data.live.alpha} · 2×2×2`, p: defaults.data.live },
    { id: 'fast', label: 'Fast (simulated clock)', sub: 'same design, back-dated clock', p: defaults.data.fast },
    { id: 'doc', label: 'Proposal scale', sub: '1,500 slots · α 0.001 · 55% leak', p: defaults.data.doc_scale },
    { id: 'null', label: 'Null control', sub: 'clean agent only · false-alarm check', p: defaults.data.null_control },
  ] : []

  return (
    <div>
      <PageHeader title="Run audit" lead="Configure a campaign and start it. Progress streams live from the database; the verdict appears only when the planned n is reached." />
      {state.campaign?.simulated_clock && running && <div className="mb-3"><Banner tone="steel"><b>Simulated clock.</b> All timestamps in this run are back-dated by the engine and labelled as such everywhere.</Banner></div>}
      <div className="grid gap-5" style={{ gridTemplateColumns: 'minmax(0, 7fr) minmax(0, 5fr)' }}>
        <Panel title="Campaign configuration" subtitle="Defaults are derived from an exact power calculation, not tuned (see the panel on the right)." bodyClass="p-4" sql={defaults.data?.sql} loading={defaults.loading} error={defaults.error} onRetry={defaults.reload}>
          {f && (
            <div className="space-y-4">
              <div className="flex flex-wrap gap-2" role="group" aria-label="Presets">
                {presets.map((p) => <button key={p.id} className="btn" disabled={running} onClick={() => setForm(fromPreset(p.p))}><span className="font-semibold">{p.label}</span><span className="text-ink-3 text-[0.78rem]">{p.sub}</span></button>)}
              </div>
              <div className="grid grid-cols-2 gap-x-5 gap-y-3.5">
                <label className="block"><span className="eyebrow">false-alarm rate alpha (family-wise)</span>
                  <select className="mt-1 w-full btn" disabled={running} value={f.alpha} onChange={(e) => set({ alpha: Number(e.target.value) })} data-testid="alpha">{[0.05, 0.01, 0.001].map((a) => <option key={a} value={a}>{a}</option>)}</select></label>
                <label className="block"><span className="eyebrow">planned slots ({cells} cell{cells > 1 ? 's' : ''} × {Number.isInteger(perCell) ? perCell : '?'})</span>
                  <input type="number" min={cells} step={cells} className={cx('mt-1 w-full btn num', !valid && 'border-leak')} disabled={running} value={f.planned_slots} onChange={(e) => set({ planned_slots: Number(e.target.value) })} data-testid="slots" />
                  {!valid && <span className="text-[0.78rem] text-leak-ink">must be a multiple of {cells}, ≥ {2 * cells}, ≤ {f.clock_mode === 'SIMULATED' ? 3500 : 1000}</span>}</label>
                <fieldset><legend className="eyebrow">clock mode</legend>
                  <div className="mt-1 flex gap-2">
                    {(['LIVE', 'SIMULATED'] as const).map((m) => <button key={m} disabled={running} onClick={() => set({ clock_mode: m })} aria-pressed={f.clock_mode === m} className={cx('btn flex-1 justify-center', f.clock_mode === m && 'btn-primary')} data-testid={`clock-${m}`}>{m === 'LIVE' ? 'Live (wall clock)' : 'Simulated clock'}</button>)}
                  </div>
                  <p className="text-[0.78rem] text-ink-3 mt-1">{f.clock_mode === 'LIVE' ? 'Real-time slots; commit-before-expose is provable on the wall clock.' : 'Back-dated simulated timestamps, for big runs. Labelled “simulated clock” everywhere.'}</p></fieldset>
                <label className="block"><span className="eyebrow">slot length{f.clock_mode === 'LIVE' ? '' : ' (simulated: 2 s)'}</span>
                  <input type="number" min={400} max={5000} step={100} className="mt-1 w-full btn num" disabled={running || f.clock_mode !== 'LIVE'} value={f.slot_ms} onChange={(e) => set({ slot_ms: Number(e.target.value) })} />
                  <span className="text-[0.78rem] text-ink-3">milliseconds, 400–5000</span></label>
                <label className="block col-span-2"><span className="eyebrow">design: which channels are switched on / off</span>
                  <select className="mt-1 w-full btn" disabled={running} value={f.design} onChange={(e) => set({ design: e.target.value, planned_slots: Math.max(cellsOfDesign(e.target.value, f), Math.round(f.planned_slots / cells) * cellsOfDesign(e.target.value, f)) })} data-testid="design">
                    <option value="FULL_FACTORIAL">2×2×2 full factorial (8 cells): channel main effects</option>
                    <option value="ONE_AT_A_TIME">one at a time (5 cells): all on, each off alone, all off</option>
                    <option value="ALL_ON">all channels on (1 cell)</option>
                    <option value="CUSTOM">custom: choose per channel</option>
                  </select></label>
                {f.design === 'CUSTOM' && (
                  <div className="col-span-2 grid grid-cols-3 gap-3">
                    {CH.map((c) => <label key={c} className="block"><span className="eyebrow">{c.replace('_', ' ')}</span>
                      <select className="mt-1 w-full btn" disabled={running} value={f.channels[c]} onChange={(e) => set({ channels: { ...f.channels, [c]: e.target.value as Mode } })}><option value="vary">varied (factor)</option><option value="on">fixed ON</option><option value="off">fixed OFF</option></select></label>)}
                  </div>
                )}
              </div>
              <div className="grid grid-cols-2 gap-x-5 gap-y-3 border-t border-line-2 pt-4">
                <fieldset className="col-span-2"><legend className="eyebrow">inference</legend>
                  <div className="mt-1 flex gap-2">
                    {(['FIXED', 'SEQUENTIAL'] as const).map((m) => <button key={m} disabled={running} onClick={() => set({ inference: m, stop_rule: m === 'FIXED' ? 'MAX' : f.stop_rule === 'MAX' ? 'FIRST_LEAK' : f.stop_rule })} aria-pressed={f.inference === m} className={cx('btn flex-1 justify-center', f.inference === m && 'btn-primary')} data-testid={`inference-${m}`}>{m === 'FIXED' ? 'Fixed n (exact tests)' : 'Sequential (anytime-valid)'}</button>)}
                  </div>
                  <p className="text-[0.78rem] text-ink-3 mt-1">{f.inference === 'FIXED' ? 'Most power at the planned n; the verdict may only be read once, at the end.' : 'E-values valid at every slot: watch the evidence live and stop early under a rule fixed in advance. Costs some power at the same n.'}</p></fieldset>
                {f.inference === 'SEQUENTIAL' && (<>
                  <label className="block"><span className="eyebrow">stopping rule (pre-registered)</span>
                    <select className="mt-1 w-full btn" disabled={running} value={f.stop_rule} onChange={(e) => set({ stop_rule: e.target.value as Form['stop_rule'] })} data-testid="stop-rule">
                      <option value="FIRST_LEAK">stop at the first agent flagged</option><option value="ALL_SETTLED">stop when every agent is settled</option><option value="MAX">never stop early</option></select></label>
                  <label className="block"><span className="flex justify-between"><span className="eyebrow">materiality (settled below)</span><span className="num text-[0.85rem]">{f.materiality.toFixed(2)}</span></span>
                    <input type="range" min={0.55} max={0.95} step={0.01} className="w-full accent-[var(--steel)]" disabled={running || f.stop_rule !== 'ALL_SETTLED'} value={f.materiality} onChange={(e) => set({ materiality: Number(e.target.value) })} />
                    <span className="text-[0.76rem] text-ink-3">an agent is settled once flagged, or once its anytime upper bound on accuracy is below this</span></label>
                </>)}
              </div>
              <div className="grid grid-cols-2 gap-x-5 gap-y-3 border-t border-line-2 pt-4">
                <div className="col-span-2 eyebrow">scripted agents: planted ground truth (validation instruments)</div>
                <label className="block"><span className="flex justify-between"><span className="eyebrow">trader-leaky trust</span><span className="num text-[0.85rem]">{f.trustLeaky.toFixed(2)} → acc {(0.5 + f.trustLeaky / 2).toFixed(2)}</span></span>
                  <input type="range" min={0} max={1} step={0.05} className="w-full accent-[var(--steel)]" disabled={running || f.null_control} value={f.trustLeaky} onChange={(e) => set({ trustLeaky: Number(e.target.value) })} data-testid="trust-leaky" /></label>
                <label className="block"><span className="flex justify-between"><span className="eyebrow">trader-partial trust</span><span className="num text-[0.85rem]">{f.trustPartial.toFixed(2)} → acc {(0.5 + f.trustPartial / 2).toFixed(2)}</span></span>
                  <input type="range" min={0} max={1} step={0.05} className="w-full accent-[var(--steel)]" disabled={running || f.null_control} value={f.trustPartial} onChange={(e) => set({ trustPartial: Number(e.target.value) })} data-testid="trust-partial" /></label>
                <label className="block"><span className="eyebrow">trader-partial reads only</span>
                  <select className="mt-1 w-full btn" disabled={running || f.null_control} value={f.partial_channel} onChange={(e) => set({ partial_channel: e.target.value })}><option value="vector_memory">vector memory</option><option value="notes_table">notes table</option><option value="feature_cache">cache</option></select></label>
                {meta.data?.llm_enabled && meta.data?.llm_wall_present && (
                  <label className="flex items-start gap-2 col-span-2"><input type="checkbox" className="w-4 h-4 mt-0.5 accent-[var(--steel)]" disabled={running || f.null_control} checked={f.llm} onChange={(e) => set({ llm: e.target.checked })} data-testid="llm" />
                    <span><span className="font-medium">Optional LLM trader</span> <span className="text-ink-3 text-[0.82rem]">own wall (WALL-2) with the clean baseline. No planted behaviour: whatever it does is reported as measured, never tuned. Needs an API key.</span></span></label>)}
                <label className="flex items-center gap-2 mt-5"><input type="checkbox" className="w-4 h-4 accent-[var(--steel)]" disabled={running} checked={f.null_control} onChange={(e) => set({ null_control: e.target.checked, llm: e.target.checked ? false : f.llm })} data-testid="null-control" />
                  <span><span className="font-medium">Null control</span> <span className="text-ink-3 text-[0.82rem]">run only trader-clean</span></span></label>
              </div>
              <div className="flex items-center gap-3 pt-1">
                {running ? <button className="btn btn-danger" onClick={() => cancel()}><IconStop size={13} />Stop run</button>
                  : <button className="btn btn-primary" disabled={!valid || busy} onClick={go} data-testid="start"><IconPlay size={13} />Start campaign</button>}
                <span className="text-[0.85rem] text-ink-2">≈ {eta < 90 ? `${Math.round(eta)} s` : `${Math.floor(eta / 60)} min ${Math.round(eta % 60)} s`} · {fmtInt(f.planned_slots)} slots</span>
              </div>
              {err && <Banner tone="leak">{err}</Banner>}
            </div>
          )}
        </Panel>

        <Panel title="Planned design: power at the planned n" subtitle="Computed in SQL from the exact binomial. NO_EVIDENCE is not “clean”: it means “below this detectable accuracy”." sql={[...gatePower.sql, ...power.sql]} loading={power.loading && !power.data} error={power.error} bodyClass="p-4">
          {f && power.data && (
            <div className="space-y-4" data-testid="power-preview">
              <div className="grid grid-cols-2 gap-4">
                <Stat label="gate: min detectable accuracy" value={gatePower.data?.point.min_detectable_acc ? fmtAcc(gatePower.data.point.min_detectable_acc) : '—'} sub={`pooled over ${fmtInt(f.planned_slots)} slots at α/${nLow} (80% power)`} />
                <Stat label={f.null_control ? 'gate power at 0.55' : `gate power at ${plantedAcc.toFixed(2)}`} value={gatePower.data ? fmtPct(gatePower.data.point.power_at_acc, 1) : '—'} sub={f.inference === 'SEQUENTIAL' ? 'fixed-n figure; sequential power is lower at the same n' : 'exact binomial, per agent'} />
                <Stat label="cell: min detectable accuracy" value={power.data.point.min_detectable_acc ? fmtAcc(power.data.point.min_detectable_acc) : '—'} sub={`${Number.isInteger(perCell) ? perCell : '?'} slots per cell, conservative level α/${K}`} />
                <Stat label={f.null_control ? 'cell power at 0.55' : `cell power at ${plantedAcc.toFixed(2)}`} value={fmtPct(power.data.point.power_at_acc, 1)} sub="lower bound: gatekeeping's Holm level is never stricter" />
              </div>
              {defaults.data?.derivation && f.design === 'FULL_FACTORIAL' && f.alpha === 0.05 && perCell === 19 && f.trustLeaky === 0.9 && (
                <Banner tone="plain">Default derived by Monte-Carlo of the Holm rule over all 24 tests ({fmtInt(defaults.data.derivation.reps)} reps): P(every truly-leaky cell flagged) = <b className="num">{fmtPct(defaults.data.derivation.default_joint_power, 1)}</b>; family-wise false-alarm probability <b className="num">{fmtPct(defaults.data.derivation.default_fwer, 1)}</b> ≤ α.</Banner>
              )}
              <div className="h-[110px]">{gatePower.data && <PowerSpark curve={gatePower.data.curve} mda={gatePower.data.point.min_detectable_acc} planted={f.null_control ? 0.55 : plantedAcc} />}</div>
              <div className="text-[0.78rem] text-ink-3">Curve: exact power of one agent's pooled gate test versus true accuracy at n = {fmtInt(f.planned_slots)}, level α/{nLow}. Dashed marks: minimum detectable accuracy and the planted accuracy.</div>
            </div>
          )}
        </Panel>
      </div>

      <div className="mt-5 grid gap-5">
        <Panel title="Live progress" subtitle="Streaming from the database (server-sent events)." bodyClass="p-4" sql={sqlLive}
          actions={state.campaign ? <Chip tone={running ? 'steel' : 'plain'}>{running ? (state.campaign.simulated_clock ? 'SIMULATED CLOCK' : 'LIVE') : state.cancelled ? 'cancelled' : state.frozen ? 'frozen' : 'finished'}</Chip> : null}>
          {!state.campaign ? <EmptyState title="No campaign running" icon={<IconPlay size={26} />}>Start a campaign above. Commitments are published before each slot opens, flips are revealed after it ends, and the cumulative chart below fills in as slots are scored.</EmptyState> : (
            <div className="space-y-4">
              <div>
                <div className="flex justify-between text-[0.88rem] mb-1.5"><span className="num"><b>{state.slotsDone}</b> / {state.campaign.planned_slots} slots scored</span><span className="text-ink-3">campaign #{state.campaign.campaign_id} · α {state.campaign.alpha} · {state.campaign.n_agents} agent gates · {sequentialRun ? `sequential, ${String(state.campaign.stop_rule).toLowerCase().replace('_', ' ')}` : 'fixed n'}</span></div>
                <div className="h-2.5 rounded-full bg-surface-3 overflow-hidden" role="progressbar" aria-valuenow={state.slotsDone} aria-valuemax={state.campaign.planned_slots}><div className="h-full bg-steel transition-[width] duration-200" style={{ width: `${pct * 100}%` }} /></div>
              </div>
              <div className="grid gap-5" style={{ gridTemplateColumns: 'minmax(0, 3fr) minmax(0, 2fr)' }}>
                {sequentialRun ? (
                  <div data-testid="evalue-chart">
                    <Banner tone="steel"><b>Anytime-valid: watching is allowed.</b> ln E per agent after every slot. Crossing the line ln({state.campaign.n_agents}/α) = {state.evThreshold?.toFixed(2) ?? '…'} is a LEAK at family-wise α, whenever it happens.{lastCheck ? <> Last stop check after {lastCheck.slots_done} slots: <b>{lastCheck.stop ? 'STOP' : 'continue'}</b> ({lastCheck.rule.toLowerCase().replace('_', ' ')}).</> : null}</Banner>
                    <div className="mt-2" style={{ minHeight: 280 }}><LineChart series={evSeries} height={270} tableName="e-values" yLabel="ln E (evidence)" reference={false} yDomain={[evMin, evMax * 1.1]}
                      hlines={state.evThreshold != null ? [{ y: state.evThreshold, label: `LEAK threshold ln(${state.campaign.n_agents}/α)` }] : []}
                      fmtY={(v) => `ln E ${v.toFixed(2)} · p ${v <= 0 ? '1' : Math.exp(-v).toExponential(1)}`} /></div>
                  </div>
                ) : (
                  <div>
                    <Banner tone="plain" icon={<IconLock size={15} className="mt-0.5 shrink-0" />}><b>Descriptive only: this is not a test.</b> Repeated looks at a fixed-n test inflate false alarms, so no p-value or verdict is shown before n = {state.campaign.planned_slots}.</Banner>
                    <div className="mt-2" style={{ minHeight: 280 }}><LineChart series={series} height={270} tableName="cumulative correct" /></div>
                  </div>)}
                <div style={{ minHeight: 330 }}>
                  <div className="eyebrow mb-1.5">recent gateway accesses (access_event)</div>
                  <div className="border border-line rounded-lg overflow-hidden" style={{ height: 300 }}>
                    <div className="overflow-auto h-full"><table className="t mono text-[0.76rem]"><tbody>
                      {state.recent.slice(0, 14).map((e) => (
                        <tr key={e.event_id}><td className="text-ink-3 num">{fmtClock(e.event_time).slice(0, 12)}</td><td>{e.agent_name.replace('trader-', '').replace('-agent', '')}</td><td>{e.op === 'READ' ? 'READ' : 'WRITE'}</td><td>{e.asset_name}</td>
                          <td className={cx(e.outcome === 'DENIED' && 'text-ink-3')}>{e.outcome === 'DENIED' ? `✕ ${e.detail}` : '✓'}</td></tr>))}
                      {state.recent.length === 0 && <tr><td className="text-ink-3 p-3" colSpan={5}>waiting for the first slot…</td></tr>}
                    </tbody></table></div>
                  </div>
                </div>
              </div>
              {!state.frozen ? (
                <div className="rounded-lg border border-dashed border-line p-3 flex items-center gap-3 text-ink-2" data-testid="verdict-withheld"><IconLock size={18} /><div><b>Verdict {sequentialRun ? 'not frozen yet' : 'withheld'}.</b> {sequentialRun ? 'It is frozen, signed, when the stopping rule fires or the planned n is reached.' : 'It is computed once, at the planned n, and frozen (signed) into the append-only audit_result table.'}</div><VerdictChip verdict={null} /></div>
              ) : frozen.data ? (
                <div className="rounded-lg border border-line p-3 flex items-center gap-4 flex-wrap" data-testid="verdict-ready">
                  <b>Verdict frozen at n = {frozen.data.agents[0]?.n_slots ?? state.campaign.planned_slots}{state.stoppedEarly ? ' (stopped early)' : ''}.</b>
                  {frozen.data.agents.map((r) => <span key={r.low_agent} className="flex items-center gap-1.5"><span className="text-ink-2">{r.low_agent.replace('trader-', '')}</span><VerdictChip verdict={r.verdict} size="sm" /><span className="text-ink-3 text-[0.78rem] num">{(groupBy(frozen.data!.cells)[r.low_agent] ?? []).filter((c) => c.verdict === 'LEAK').length}/{(groupBy(frozen.data!.cells)[r.low_agent] ?? []).length} cells</span></span>)}
                  {state.snapshot && <Chip tone="steel" title={`Ed25519 signature over the snapshot; evidence root ${state.snapshot.evidence_root}`}>signed · root {state.snapshot.evidence_root.slice(0, 8)}…</Chip>}
                  <Link className="btn btn-sm ml-auto" to={`/verdicts/${state.campaign.campaign_id}`}>Open statistics →</Link>
                </div>
              ) : null}
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}

function cellsOfDesign(d: string, f: Form) { return d === 'FULL_FACTORIAL' ? 8 : d === 'ONE_AT_A_TIME' ? 5 : d === 'ALL_ON' ? 1 : cellsOf({ ...f, design: d }) }
function groupBy<T extends { low_agent: string }>(rows: T[]): Record<string, T[]> { const o: Record<string, T[]> = {}; for (const r of rows) (o[r.low_agent] ||= []).push(r); return o }

function PowerSpark({ curve, mda, planted }: { curve: { accuracy: number; power: number }[]; mda: number | null; planted: number }) {
  const w = 480, h = 110, m = { l: 30, r: 6, t: 6, b: 18 }
  const x = (a: number) => m.l + ((a - 0.5) / 0.45) * (w - m.l - m.r), y = (p: number) => m.t + (1 - p) * (h - m.t - m.b)
  const d = curve.map((c, i) => `${i ? 'L' : 'M'}${x(c.accuracy).toFixed(1)},${y(c.power).toFixed(1)}`).join('')
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-full" role="img" aria-label="Power versus true accuracy">
      {[0, 0.5, 0.8, 1].map((p) => <g key={p}><line x1={m.l} x2={w - m.r} y1={y(p)} y2={y(p)} stroke="var(--grid)" /><text x={m.l - 4} y={y(p)} dy=".32em" fontSize="9.5" textAnchor="end" fill="var(--ink-3)">{p}</text></g>)}
      {[0.5, 0.6, 0.7, 0.8, 0.9].map((a) => <text key={a} x={x(a)} y={h - 4} fontSize="9.5" textAnchor="middle" fill="var(--ink-3)">{a}</text>)}
      <path d={d} fill="none" stroke="var(--steel)" strokeWidth="2" />
      {mda && <line x1={x(mda)} x2={x(mda)} y1={m.t} y2={h - m.b} stroke="var(--ink-2)" strokeDasharray="3 3" />}
      <line x1={x(planted)} x2={x(planted)} y1={m.t} y2={h - m.b} stroke="var(--ink-3)" strokeDasharray="1 3" />
    </svg>
  )
}
