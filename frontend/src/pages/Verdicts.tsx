import { useEffect, useMemo } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Banner, Chip, EmptyState, IconCheck, PageHeader, Panel, VerdictChip } from '../components/ui'
import { AGENT_BLURB, AGENT_COLOR, AGENT_DASH, CHANNEL_LABEL, Campaign, Effect, FrozenRow, LiveRow, Resp, VerdictsData, useApi } from '../lib/api'
import { cellLabel, cellName, cx, fmtAcc, fmtBits, fmtInt, fmtP, fmtTime } from '../lib/format'

function CiBar({ lo, acc }: { lo: number; acc: number }) {
  const x = (v: number) => 4 + ((v - 0.3) / 0.7) * 112
  return (
    <svg width="124" height="18" role="img" aria-label={`accuracy ${acc.toFixed(2)}, lower bound ${lo.toFixed(2)}`}>
      <line x1={x(0.5)} x2={x(0.5)} y1="1" y2="17" stroke="var(--ink-3)" strokeDasharray="2 2" />
      <line x1={x(Math.max(0.3, lo))} x2={x(acc)} y1="9" y2="9" stroke="var(--ink-2)" strokeWidth="2" strokeLinecap="round" />
      <line x1={x(Math.max(0.3, lo))} x2={x(Math.max(0.3, lo))} y1="5" y2="13" stroke="var(--ink-2)" strokeWidth="2" />
      <circle cx={x(acc)} cy="9" r="4" fill="var(--ink)" stroke="var(--surface)" strokeWidth="2" />
    </svg>
  )
}

function Grid({ agent, rows, effects, full }: { agent: string; rows: (FrozenRow | LiveRow)[]; effects: Effect[]; full: boolean }) {
  const cell = (v: boolean, n: boolean, c: boolean) => rows.find((r) => r.vector_memory_on === v && r.notes_table_on === n && r.cache_on === c)
  const acc = (r: FrozenRow | LiveRow) => ('accuracy' in r ? r.accuracy : 0)
  const verdict = (r: FrozenRow | LiveRow) => ('verdict' in r ? r.verdict : null)
  const Tile = ({ r }: { r?: FrozenRow | LiveRow }) => r ? (
    <div className={cx('rounded-md border px-2 py-1.5 h-[52px] flex flex-col justify-center', verdict(r) === 'LEAK' ? 'bg-leak-bg border-leak-line' : 'bg-surface-2 border-line')} data-cell-verdict={verdict(r) ?? 'WITHHELD'} title={`${cellName(r)}: ${r.n_correct}/${r.n_slots}`}>
      <div className={cx('num font-semibold leading-5', verdict(r) === 'LEAK' && 'text-leak-ink')}>{fmtAcc(acc(r))}</div>
      <div className="num text-[0.72rem] text-ink-3 leading-4">{r.n_correct}/{r.n_slots}{verdict(r) === 'LEAK' ? ' · LEAK' : ''}</div>
    </div>) : <div className="h-[52px] rounded-md border border-dashed border-line" />
  const eff = effects.filter((e) => e.low_agent === agent)
  const maxAbs = Math.max(0.25, ...eff.map((e) => Math.abs(e.main_effect ?? 0)))
  return (
    <div className="card p-3.5" data-testid={`grid-${agent}`}>
      <div className="flex items-center gap-2 mb-0.5">
        <svg width="24" height="8" aria-hidden><line x1="0" y1="4" x2="24" y2="4" stroke={AGENT_COLOR[agent]} strokeWidth="2.5" strokeDasharray={AGENT_DASH[agent]} strokeLinecap="round" /></svg>
        <span className="font-semibold">{agent}</span>
      </div>
      <div className="text-[0.78rem] text-ink-3 mb-2.5">{AGENT_BLURB[agent]}</div>
      {full ? (
        <div className="grid gap-1.5" style={{ gridTemplateColumns: '74px 1fr 1fr' }}>
          <div /><div className="eyebrow text-center">cache on</div><div className="eyebrow text-center">cache off</div>
          {([[true, true], [true, false], [false, true], [false, false]] as const).map(([v, n]) => (
            <div key={`${v}${n}`} className="contents">
              <div className="text-[0.72rem] text-ink-3 leading-4 self-center">{v ? 'vector' : '—'}{' + '}{n ? 'notes' : '—'}</div>
              <Tile r={cell(v, n, true)} /><Tile r={cell(v, n, false)} />
            </div>))}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-1.5">{rows.map((r, i) => <div key={i}><div className="text-[0.72rem] text-ink-3 mb-0.5">{cellName(r)}</div><Tile r={r} /></div>)}</div>
      )}
      <div className="mt-3.5">
        <div className="eyebrow mb-1.5">main effect: accuracy with channel on − off</div>
        {eff.every((e) => e.main_effect == null) ? <div className="text-[0.82rem] text-ink-3">Not estimable: no channel was switched off in this design.</div> : <div className="space-y-1.5">
          {eff.map((e) => {
            const d = e.main_effect
            return (
              <div key={e.channel} className="grid items-center gap-2" style={{ gridTemplateColumns: '92px 1fr 52px' }} data-effect={`${agent}:${e.channel}`}>
                <span className="text-[0.82rem] text-ink-2">{CHANNEL_LABEL[e.channel]}</span>
                <svg width="100%" height="14" preserveAspectRatio="none" role="img" aria-label={`${e.channel} main effect ${d == null ? 'n/a' : d.toFixed(2)}`}>
                  <line x1="50%" x2="50%" y1="0" y2="14" stroke="var(--ink-3)" strokeWidth="1" />
                  {d != null && <rect x={d >= 0 ? '50%' : `${50 + (d / maxAbs) * 50}%`} width={`${(Math.abs(d) / maxAbs) * 50}%`} y="2" height="10" rx="2" fill="var(--steel)" opacity={Math.abs(d) < 0.1 ? 0.45 : 1} />}
                </svg>
                <span className="num text-[0.82rem] text-right">{d == null ? '—' : (d >= 0 ? '+' : '') + d.toFixed(2)}</span>
              </div>)
          })}
        </div>}
      </div>
    </div>
  )
}

export default function Verdicts() {
  const { cid } = useParams()
  const nav = useNavigate()
  const campaigns = useApi<Campaign[]>('/api/campaigns')
  const latest = useApi<{ campaign_id: number | null }>('/api/campaigns/latest')
  const id = cid ? Number(cid) : latest.data?.campaign_id ?? null
  useEffect(() => { if (!cid && latest.data?.campaign_id) nav(`/verdicts/${latest.data.campaign_id}`, { replace: true }) }, [cid, latest.data, nav])
  const v = useApi<VerdictsData>(id ? `/api/campaigns/${id}/verdicts` : null, [id])
  const d = v.data
  const byAgent = useMemo(() => {
    const o: Record<string, (FrozenRow | LiveRow)[]> = {}
    for (const r of (d?.frozen.length ? d.frozen : d?.live) ?? []) (o[r.low_agent] ||= []).push(r)
    return o
  }, [d])
  const agents = Object.keys(byAgent).sort()
  const full = d?.campaign.config?.design === 'FULL_FACTORIAL'
  const frozen = !!d?.frozen.length
  const allOk = d?.frozen.every((r) => r.hash_ok)

  return (
    <div>
      <PageHeader title="Verdicts" lead="Per campaign, per treatment cell and per trading agent: accuracy, exact binomial p-value, Holm-adjusted p across the whole family, a Clopper–Pearson lower bound and the matching leakage bound. The verdict uses the adjusted p."
        right={<select className="btn" value={id ?? ''} onChange={(e) => nav(`/verdicts/${e.target.value}`)} aria-label="Campaign" data-testid="campaign-select">
          {(campaigns.data ?? []).map((c) => <option key={c.campaign_id} value={c.campaign_id}>#{c.campaign_id} · {c.status} · {c.planned_slots} slots · {c.clock_mode === 'SIMULATED' ? 'simulated' : 'live'}</option>)}
          {!campaigns.data?.length && <option value="">no campaigns</option>}</select>} />
      {!id && !v.loading && !latest.loading ? <div className="card"><EmptyState title="No campaign yet">Run an audit from the Wall or the Run audit page; verdicts are frozen into audit_result when the planned n is reached.</EmptyState></div> : (
        <div className="grid gap-5">
          {d && (
            <div className="card px-4 py-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-[0.88rem]" data-testid="campaign-meta">
              <span><span className="text-ink-3">campaign</span> <b className="num">#{d.campaign.campaign_id}</b></span>
              <span><span className="text-ink-3">wall</span> {d.campaign.wall_name}</span>
              <span><span className="text-ink-3">α (family-wise)</span> <b className="num">{d.campaign.alpha}</b></span>
              <span><span className="text-ink-3">planned slots</span> <b className="num">{fmtInt(d.campaign.planned_slots)}</b> in {d.campaign.n_cells} cell{d.campaign.n_cells > 1 ? 's' : ''}</span>
              <span><span className="text-ink-3">family K</span> <b className="num">{d.campaign.n_cells * (d.campaign.n_low ?? 1)}</b></span>
              {d.campaign.clock_mode === 'SIMULATED' ? <Chip tone="steel">simulated clock</Chip> : <Chip>live clock</Chip>}
              <Chip tone={d.campaign.status === 'CLOSED' ? 'solid' : 'plain'}>{d.campaign.status}</Chip>
              {d.no_trade.some((x) => x.n_no_trade > 0) && <Chip title="Slots with no net position count as wrong. In a LIVE run a late order (slow machine) lands here." className="gap-1">no-trade slots: {d.no_trade.filter((x) => x.n_no_trade > 0).map((x) => `${x.low_agent.replace('trader-', '')} ${x.n_no_trade}/${x.n_slots}`).join(' · ')}</Chip>}
              {frozen && <span className="flex items-center gap-1 text-ink-2" title="result_hash is recomputed from the stored fields"><IconCheck size={14} className="text-steel" />snapshot hashes {allOk ? 'verified' : <b className="text-leak">MISMATCH</b>}</span>}
              <span className="text-ink-3 ml-auto">{fmtTime(d.campaign.closed_at ?? d.campaign.started_at)}</span>
            </div>)}

          <Panel title="Verdict per agent and treatment" loading={v.loading && !d} error={v.error} onRetry={v.reload} sql={v.sql.filter((s) => s.name.startsWith('verdicts'))}
            subtitle={frozen ? 'Frozen in the append-only audit_result table. p raw = exact one-sided binomial; the verdict uses p adjusted (Holm over every cell × agent).' : 'Not frozen yet: inferential columns stay empty until the planned n is reached.'}>
            {d && (
              <div className="overflow-auto" style={{ maxHeight: 560 }}>
                <table className="t num" data-testid="verdict-table">
                  <thead><tr><th>agent</th><th>cell (V N C)</th><th className="r">n</th><th className="r">correct</th><th className="r">acc.</th><th title="Clopper–Pearson one-sided lower bound at level α/K; dashed line = 0.5">lower bound (CP)</th><th className="r">p raw</th><th className="r">p adj. (Holm)</th><th className="r">bits (est · lower)</th><th>verdict</th><th className="r" title="minimum detectable accuracy, 80% power">MDA</th></tr></thead>
                  <tbody>
                    {frozen ? d.frozen.map((r, i) => (
                      <tr key={r.result_id} className={cx(i > 0 && d.frozen[i - 1].low_agent !== r.low_agent && 'border-t-2 border-line')} data-verdict={r.verdict} data-agent={r.low_agent}>
                        <td className="font-medium">{r.low_agent}</td><td className="mono" title={cellName(r)}>{cellLabel(r)}</td>
                        <td className="r">{r.n_slots}</td><td className="r">{r.n_correct}</td><td className="r">{fmtAcc(r.accuracy)}</td>
                        <td className="whitespace-nowrap"><span className="inline-flex items-center gap-1"><CiBar lo={r.acc_lower} acc={r.accuracy} /><span className="text-[0.76rem] text-ink-3 w-9">{fmtAcc(r.acc_lower)}</span></span></td>
                        <td className="r">{fmtP(r.p_value, r.log10_p)}</td><td className="r font-medium">{fmtP(r.p_adjusted)}</td>
                        <td className="r">{fmtBits(r.leakage_bits)} · <b className={cx(r.verdict === 'LEAK' && 'text-leak')}>{fmtBits(r.leakage_bits_lower)}</b></td>
                        <td><VerdictChip verdict={r.verdict} size="sm" /></td>
                        <td className="r">{r.verdict === 'NO_EVIDENCE' ? (r.min_detectable_acc != null ? fmtAcc(r.min_detectable_acc) : <span title="cannot reject at this n and level, even with every slot correct" className="text-ink-2">none</span>) : <span className="text-ink-3">—</span>}</td>
                      </tr>)) : d.live.map((r, i) => (
                      <tr key={i} data-verdict="WITHHELD"><td className="font-medium">{r.low_agent}</td><td className="mono">{cellLabel(r)}</td><td className="r">{r.n_slots}</td><td className="r">{r.n_correct}</td><td className="r">{fmtAcc(r.accuracy)} <span className="text-ink-3 text-[0.72rem]">descriptive</span></td><td /><td className="r">—</td><td className="r">—</td><td className="r">—</td><td><VerdictChip verdict={null} size="sm" /></td><td className="r">—</td></tr>))}
                    {!d.frozen.length && !d.live.length && <tr><td colSpan={11}><EmptyState title="No scored slots yet" /></td></tr>}
                  </tbody>
                </table>
              </div>)}
          </Panel>

          <div className="grid gap-3.5 xl:grid-cols-[1fr_1fr_1fr]" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }}>
            {d && agents.length > 0 && (
              <div className="col-span-full"><Panel title="Which channel carried the leak? Factorial attribution" sql={v.sql.filter((s) => s.name === 'channel_effect')} bodyClass="p-4"
                subtitle="Each tile is one treatment cell (a channel combination switched on or off). A LEAK outline means that cell's Holm-adjusted p ≤ α. Bars: pooled accuracy with the channel on minus off, a descriptive contrast.">
                <div className="grid gap-3.5" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }}>
                  {agents.map((a) => <Grid key={a} agent={a} rows={byAgent[a]} effects={d.channel_effect} full={full} />)}
                </div>
                <div className="mt-3 text-[0.82rem] text-ink-2 leading-5">Reading it: a channel is implicated when switching it off removes the leak. An agent that reads several channels redundantly (trader-leaky) keeps leaking until <i>all</i> of them are off, so its single-channel main effects are small by design; a single-channel agent (trader-partial) loses its leak exactly when its channel is off.</div>
              </Panel></div>)}
          </div>

          {d && (
            <div className="grid gap-5" style={{ gridTemplateColumns: 'minmax(0, 3fr) minmax(0, 2fr)' }}>
              <Panel title="Gateway accesses in this campaign" sql={v.sql.filter((s) => s.name === 'access_summary')} subtitle="Every read and write went through the logged gateway. Switched-off channels and missing grants are logged as DENIED." bodyClass="max-h-[300px] overflow-auto">
                <table className="t num"><thead><tr><th>asset</th><th>op</th><th>outcome</th><th className="r">events</th></tr></thead><tbody>
                  {d.access_summary.map((a, i) => <tr key={i}><td className="mono">{a.asset_name}</td><td>{a.op}</td><td>{a.outcome === 'DENIED' ? <span className="text-ink-2">✕ denied</span> : 'allowed'}</td><td className="r">{fmtInt(a.n)}</td></tr>)}
                  {d.access_summary.length === 0 && <tr><td colSpan={4} className="text-ink-3 p-4">No events.</td></tr>}</tbody></table>
              </Panel>
              <Panel title="How to read this" bodyClass="p-4 text-[0.9rem] text-ink-2 space-y-2.5">
                <p><b className="text-ink">No peeking.</b> The verdict is computed once, at the planned n. Live charts elsewhere are descriptive only.</p>
                <p><b className="text-ink">Multiplicity.</b> {d.campaign.n_cells} cells × {d.campaign.n_low ?? 1} agent{(d.campaign.n_low ?? 1) > 1 ? 's' : ''} = {d.campaign.n_cells * (d.campaign.n_low ?? 1)} tests, controlled with Holm–Bonferroni at family-wise α = {d.campaign.alpha}.</p>
                <p><b className="text-ink">Bounds.</b> The point estimate is not a bound: the lower bound is one-sided Clopper–Pearson at level α/K (valid simultaneously); Holm can reject where that conservative bound is still 0.</p>
                <p><b className="text-ink">NO EVIDENCE ≠ clean.</b> It means the test had no power below the minimum detectable accuracy (MDA column).</p>
              </Panel>
            </div>)}
        </div>)}
    </div>
  )
}
