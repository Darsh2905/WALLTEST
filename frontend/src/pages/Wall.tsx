import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import Hero from '../components/Hero'
import { Banner, Chip, EmptyState, IconCheck, IconLock, IconPlay, IconStop, Panel, PageHeader, VerdictChip, Skeleton } from '../components/ui'
import { AGENT_BLURB, AGENT_COLOR, AGENT_DASH, FrozenRow, Resp, SqlMeta, VerdictsData, useApi } from '../lib/api'
import { useRun } from '../lib/run'
import { cx, fmtAcc, fmtBits, fmtP, fmtPct, shortHash } from '../lib/format'

/** headline row for an agent: the all-channels-on cell (the wall as configured), else the first cell */
export function headline(rows: FrozenRow[]): FrozenRow | undefined {
  return rows.find((r) => r.vector_memory_on && r.notes_table_on && r.cache_on) ?? rows[0]
}

export function VerdictCard({ agent, rows, config, live }: { agent: string; rows: FrozenRow[]; config: any; live?: { n: number; correct: number; planned: number } | null }) {
  const h = rows.length ? headline(rows) : undefined
  const leakCells = rows.filter((r) => r.verdict === 'LEAK').length
  const trust = config?.trust?.[agent]
  const chans: string[] = config?.channels?.[agent] ?? []
  const planted = agent === 'trader-clean' ? 'planted: no signal use (price momentum only)' : trust != null ? `planted: trust ${Number(trust).toFixed(2)} → expected accuracy ${(0.5 + Number(trust) / 2).toFixed(2)} · channels ${chans.map((c) => c.replace('_memory', '').replace('_table', '').replace('feature_', '')).join(', ')}` : ''
  return (
    <div className="card p-4 flex flex-col gap-3" style={{ minHeight: 252 }} data-testid={`verdict-card-${agent}`} data-verdict={h?.verdict ?? 'WITHHELD'}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <svg width="26" height="8" aria-hidden><line x1="0" y1="4" x2="26" y2="4" stroke={AGENT_COLOR[agent]} strokeWidth="2.5" strokeDasharray={AGENT_DASH[agent]} strokeLinecap="round" /></svg>
            <span className="font-semibold">{agent}</span>
          </div>
          <div className="text-[0.82rem] text-ink-3">{AGENT_BLURB[agent]}</div>
        </div>
        <VerdictChip verdict={h?.verdict} size="lg" />
      </div>
      {h ? (
        <>
          <div className="grid grid-cols-3 gap-3">
            <div><div className="eyebrow">accuracy</div><div className="num text-[1.35rem] font-semibold leading-7">{fmtAcc(h.accuracy)}</div><div className="num text-[0.78rem] text-ink-3">{h.n_correct}/{h.n_slots}</div></div>
            <div><div className="eyebrow">p (Holm adj.)</div><div className="num text-[1.35rem] font-semibold leading-7">{fmtP(h.p_adjusted)}</div><div className="num text-[0.78rem] text-ink-3">raw {fmtP(h.p_value, h.log10_p)}</div></div>
            <div><div className="eyebrow">leakage bound</div><div className={cx('num text-[1.35rem] font-semibold leading-7', h.verdict === 'LEAK' && 'text-leak')}>{fmtBits(h.leakage_bits_lower)}<span className="text-[0.8rem] font-normal text-ink-3"> bits</span></div><div className="num text-[0.78rem] text-ink-3">est. {fmtBits(h.leakage_bits)} · lower bound</div></div>
          </div>
          <div className="text-[0.85rem] text-ink-2 leading-5">
            {rows.length > 1 && <div><b className="num">{leakCells}</b> of <b className="num">{rows.length}</b> channel cells flagged LEAK{h.vector_memory_on && h.notes_table_on && h.cache_on ? ' · headline = all channels on' : ''}.</div>}
            {h.verdict === 'NO_EVIDENCE' && (h.min_detectable_acc != null
              ? <div>Not proof of absence: at n = {h.n_slots}/cell this test detects ≥ <b className="num">{fmtAcc(h.min_detectable_acc)}</b> accuracy with 80% power.</div>
              : <div>Underpowered: at n = {h.n_slots}/cell the test cannot reject at α/K even with every slot correct. This says nothing about leakage.</div>)}
          </div>
        </>
      ) : live ? (
        <div className="flex-1 flex flex-col justify-center gap-1">
          <div className="num text-[1.35rem] font-semibold">{live.n ? fmtPct(live.correct / live.n, 0) : '—'} <span className="text-[0.85rem] font-normal text-ink-3">descriptive, so far</span></div>
          <div className="text-[0.85rem] text-ink-2">The verdict is computed once, at the planned n ({live.planned} slots). Peeking would inflate false alarms.</div>
        </div>
      ) : (
        <div className="flex-1 flex items-center text-ink-3 text-[0.9rem]">No verdict yet. Run an audit to measure this agent.</div>
      )}
      {planted && <div className="mt-auto pt-2 border-t border-line-2 text-[0.76rem] text-ink-3">{planted}</div>}
    </div>
  )
}

function SlotCell({ s, to }: { s: { index: number; slot_id: number; commitment: string; state: string; flip_bit?: number | null; ok?: boolean | null }; to: string }) {
  const sealed = s.state === 'committed' || s.state === 'SEALED'
  const open = s.state === 'open' || s.state === 'OPEN'
  return (
    <Link to={to} data-slot-state={sealed ? 'committed' : open ? 'open' : 'revealed'} title={`slot ${s.slot_id}: ${s.commitment}`}
      className={cx('shrink-0 rounded-md border px-2 py-1.5 w-[74px] flex flex-col gap-0.5 hover:bg-surface-3', open ? 'border-steel bg-steel-bg' : 'border-line bg-surface')}>
      <div className="flex items-center justify-between text-[0.7rem] text-ink-3 num"><span>#{s.index + 1}</span>{sealed ? <IconLock size={11} /> : open ? <span className="live-dot" style={{ width: 6, height: 6 }} /> : s.ok ? <IconCheck size={12} className="text-steel" /> : <span className="text-leak">✕</span>}</div>
      <div className="mono text-[0.76rem] leading-4 text-ink-2">{shortHash(s.commitment, 6)}</div>
      <div className="text-[0.66rem] uppercase tracking-wider text-ink-3 leading-3">{sealed ? 'committed' : open ? 'open' : `flip ${s.flip_bit ?? '?'} ✓`}</div>
    </Link>
  )
}

export default function Wall() {
  const { state, start, cancel } = useRun()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const defaults = useApi<any>('/api/defaults')
  const wall = useApi<{ members: any[]; grants: any[]; grant_review_rows: number }>('/api/wall')
  const latest = useApi<{ campaign_id: number | null }>('/api/campaigns/latest', [state.frozen])
  const cid = state.campaign && state.frozen ? state.campaign.campaign_id : latest.data?.campaign_id ?? null
  const verdicts = useApi<VerdictsData>(cid ? `/api/campaigns/${cid}/verdicts` : null, [cid, state.frozen])
  const slotsApi = useApi<any[]>(cid && !state.campaign ? `/api/campaigns/${cid}/slots?limit=2000` : null)
  const running = !!state.campaign && !state.finished

  const timeline = useMemo(() => {
    if (state.campaign) return state.order.slice(-48).map((i) => state.slots[i]).filter(Boolean).map((s) => ({ index: s.index, slot_id: s.slot_id, commitment: s.commitment, state: s.state, flip_bit: s.flip_bit, ok: s.commitment_ok }))
    return (slotsApi.data ?? []).slice(-48).map((s: any) => ({ index: s.idx, slot_id: s.slot_id, commitment: s.commitment, state: s.state, flip_bit: s.flip_bit, ok: s.commitment_ok }))
  }, [state.order, state.slots, state.campaign, slotsApi.data])

  async function launch(kind: 'live' | 'null') {
    setBusy(true); setErr(null)
    try {
      const d = defaults.data
      const base = kind === 'live' ? d.live : d.null_control
      await start({ ...base, null_control: kind === 'null' })
    } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }

  const agentRows = useMemo(() => {
    const by: Record<string, FrozenRow[]> = {}
    for (const r of verdicts.data?.frozen ?? []) (by[r.low_agent] ||= []).push(r)
    return by
  }, [verdicts.data])
  const liveAgents = state.campaign && !state.frozen ? Object.keys(state.agents) : null
  const agentNames = liveAgents ?? (Object.keys(agentRows).length ? Object.keys(agentRows) : ['trader-leaky', 'trader-partial', 'trader-clean'])
  const config = state.campaign && !state.frozen ? { trust: state.campaign.trust, channels: Object.fromEntries(state.campaign.agents.map((a: string) => [a, a === 'trader-leaky' ? ['vector_memory', 'notes_table', 'feature_cache'] : a === 'trader-partial' ? ['vector_memory'] : []])) } : verdicts.data?.campaign.config
  const live = (a: string) => (state.campaign && !state.frozen ? { n: state.agents[a]?.n ?? 0, correct: state.agents[a]?.correct ?? 0, planned: state.campaign.planned_slots } : null)
  const sqlWall: SqlMeta[] = wall.sql

  return (
    <div>
      <PageHeader title="The wall" lead="A research agent holds confidential (synthetic) deal information. Trading agents must never benefit from it. The audit tests that by experiment, through the shared channels that sit in the wall."
        right={
          <div className="flex items-center gap-2">
            {running ? <button className="btn btn-danger" onClick={() => cancel()}><IconStop size={13} />Stop run</button> : (<>
              <button className="btn btn-primary" disabled={busy || defaults.loading || !defaults.data} onClick={() => launch('live')} data-testid="run-live"><IconPlay size={13} />Run live audit</button>
              <button className="btn" disabled={busy || defaults.loading || !defaults.data} onClick={() => launch('null')} data-testid="run-null" title="Only the price-driven clean trader: shows false-alarm control">Null control</button>
            </>)}
          </div>} />
      {err && <div className="mb-3"><Banner tone="leak">{err}</Banner></div>}
      {state.error && <div className="mb-3"><Banner tone="leak">Run failed: {state.error}</Banner></div>}
      {state.campaign?.simulated_clock && <div className="mb-3"><Banner tone="steel"><b>Simulated clock.</b> Slot timestamps are back-dated; commit-before-expose is checked by the database against that simulated clock and cannot be proven on the wall clock. Use LIVE mode for that.</Banner></div>}

      <div className="grid gap-5">
        <Panel title="Information barrier" sql={sqlWall.length ? sqlWall : undefined} bodyClass="p-3"
          subtitle={running ? 'Dots are real access_event rows read from the database as they happen.' : state.campaign ? `Last run: campaign #${state.campaign.campaign_id}${state.campaign.simulated_clock ? ' (simulated clock)' : ''}. Counts on the gates are its real gateway accesses.` : 'Idle. Start an audit to watch real accesses cross the gates.'}
          actions={running ? <Chip tone="steel" className="gap-1.5"><span className="live-dot" />{state.campaign.clock_mode === 'LIVE' ? 'LIVE' : 'SIMULATED CLOCK'}</Chip> : wall.data ? <Chip title="Grant review must return zero rows">{wall.data.grant_review_rows === 0 ? 'grant review: 0 rows ✓' : `grant review: ${wall.data.grant_review_rows} rows`}</Chip> : null}>
          {wall.loading && !wall.data ? <div className="p-3"><Skeleton h={340} /></div> : <Hero state={state} grants={wall.data?.grants ?? null} />}
        </Panel>

        <Panel title="Slot timeline" subtitle={<>sealed <b>→</b> committed hash <b>→</b> revealed with a verified tick. Each tick is the database re-hashing the revealed flip; the browser re-checks it on the Commit–reveal page.</>}
          bodyClass="px-4 py-3" sql={undefined}>
          <div className="flex gap-2 overflow-x-auto pb-1" style={{ minHeight: 78 }} data-testid="timeline">
            {timeline.length === 0 ? <div className="text-ink-3 text-[0.9rem] self-center">No slots yet.</div> : timeline.map((s) => <SlotCell key={s.slot_id} s={s} to={`/inspector/${state.campaign?.campaign_id ?? cid}?slot=${s.slot_id}`} />)}
          </div>
        </Panel>

        <div>
          <div className="flex items-end justify-between mb-2">
            <h2 className="font-semibold text-[1.05rem]">Verdict per trading agent</h2>
            <div className="text-[0.82rem] text-ink-3">{verdicts.data ? <>campaign #{verdicts.data.campaign.campaign_id} · {verdicts.data.campaign.clock_mode === 'SIMULATED' ? 'simulated clock' : 'live clock'} · α = {verdicts.data.campaign.alpha} · <Link className="underline" to={`/verdicts/${verdicts.data.campaign.campaign_id}`}>full statistics</Link></> : liveAgents ? 'withheld until the planned n' : ''}</div>
          </div>
          <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))' }}>
            {agentNames.sort().map((a) => <VerdictCard key={a} agent={a} rows={agentRows[a] ?? []} config={config} live={live(a)} />)}
          </div>
          {!verdicts.data && !liveAgents && !verdicts.loading && (
            <div className="mt-3"><EmptyState title="No audit has been frozen yet">Press “Run live audit”. The default campaign is a 2×2×2 factorial of 152 slots chosen from an exact power calculation (see Run audit).</EmptyState></div>
          )}
        </div>
      </div>
    </div>
  )
}
