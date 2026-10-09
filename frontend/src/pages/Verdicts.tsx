import { useEffect, useMemo } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Banner, Chip, EmptyState, IconCheck, IconLock, PageHeader, Panel, VerdictChip } from '../components/ui'
import { Interval } from '../components/Interval'
import { EvidencePanel } from '../components/EvidencePanel'
import { AGENT_BLURB, AGENT_COLOR, AGENT_DASH, AnyRow, CHANNEL_LABEL, Campaign, Effect, VerdictsData, pAdj, pRaw, useApi } from '../lib/api'
import { bitsOf, cellLabel, cellName, cx, fmtAcc, fmtBits, fmtInt, fmtP, fmtTime } from '../lib/format'

const isCellRow = (r: AnyRow): r is AnyRow & { vector_memory_on: boolean; notes_table_on: boolean; cache_on: boolean } => r.vector_memory_on != null

function Grid({ agent, rows, effects, full, gate }: { agent: string; rows: AnyRow[]; effects: Effect[]; full: boolean; gate: boolean | null }) {
  const cells = rows.filter(isCellRow)
  const cell = (v: boolean, n: boolean, c: boolean) => cells.find((r) => r.vector_memory_on === v && r.notes_table_on === n && r.cache_on === c)
  const Tile = ({ r }: { r?: AnyRow }) => r ? (
    <div className={cx('rounded-md border px-2 py-1.5 h-[52px] flex flex-col justify-center', r.verdict === 'LEAK' ? 'bg-leak-bg border-leak-line' : 'bg-surface-2 border-line')} data-cell-verdict={r.verdict ?? 'WITHHELD'} title={isCellRow(r) ? `${cellName(r)}: ${r.n_correct}/${r.n_slots}` : ''}>
      <div className={cx('num font-semibold leading-5', r.verdict === 'LEAK' && 'text-leak-ink')}>{fmtAcc(r.accuracy)}</div>
      <div className="num text-[0.72rem] text-ink-3 leading-4">{r.n_correct}/{r.n_slots}{r.verdict === 'LEAK' ? ' · LEAK' : ''}</div>
    </div>) : <div className="h-[52px] rounded-md border border-dashed border-line" />
  const eff = effects.filter((e) => e.low_agent === agent)
  const maxAbs = Math.max(0.25, ...eff.map((e) => Math.abs(e.main_effect ?? 0)))
  return (
    <div className="card p-3.5" data-testid={`grid-${agent}`}>
      <div className="flex items-center gap-2 mb-0.5">
        <svg width="24" height="8" aria-hidden><line x1="0" y1="4" x2="24" y2="4" stroke={AGENT_COLOR[agent]} strokeWidth="2.5" strokeDasharray={AGENT_DASH[agent]} strokeLinecap="round" /></svg>
        <span className="font-semibold">{agent}</span>
        {gate === false && <Chip title="This agent's pooled test did not reject, so none of its cells or channels can be flagged">gate closed</Chip>}
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
        <div className="grid grid-cols-2 gap-1.5">{cells.map((r, i) => <div key={i}><div className="text-[0.72rem] text-ink-3 mb-0.5">{cellName(r)}</div><Tile r={r} /></div>)}</div>
      )}
      <div className="mt-3.5">
        <div className="eyebrow mb-1.5">main effect: accuracy with channel on − off (descriptive)</div>
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

const PCell = ({ r, seq }: { r: AnyRow; seq: boolean }) => (
  <td className="r" title={seq && r.log_e != null ? `ln E = ${r.log_e.toFixed(2)}; anytime-valid p = 1/E` : undefined}>
    {fmtP(pRaw(r), 'log10_p' in r ? r.log10_p : null)}{seq && r.log_e != null && <span className="block text-[0.7rem] text-ink-3">E = {r.log_e > 20 ? `e^${r.log_e.toFixed(0)}` : Math.exp(r.log_e).toPrecision(3)}</span>}
  </td>)

export default function Verdicts() {
  const { cid } = useParams()
  const nav = useNavigate()
  const campaigns = useApi<Campaign[]>('/api/campaigns')
  const latest = useApi<{ campaign_id: number | null }>('/api/campaigns/latest')
  const id = cid ? Number(cid) : latest.data?.campaign_id ?? null
  useEffect(() => { if (!cid && latest.data?.campaign_id) nav(`/verdicts/${latest.data.campaign_id}`, { replace: true }) }, [cid, latest.data, nav])
  const v = useApi<VerdictsData>(id ? `/api/campaigns/${id}/verdicts` : null, [id])
  const d = v.data
  const seq = d?.inference === 'SEQUENTIAL'
  const gate = useMemo(() => Object.fromEntries((d?.agents ?? []).map((a) => [a.low_agent, a.gate_passed])), [d])
  const cellsByAgent = useMemo(() => {
    const o: Record<string, AnyRow[]> = {}
    for (const r of d?.cells ?? []) (o[r.low_agent] ||= []).push(r)
    return o
  }, [d])
  const agents = Object.keys(cellsByAgent).sort()
  const full = d?.campaign.config?.design === 'FULL_FACTORIAL'
  const allRows: AnyRow[] = d ? [...d.agents, ...d.cells, ...d.channels] : []
  const allOk = d?.is_frozen && allRows.every((r) => 'hash_ok' in r ? r.hash_ok : true)
  const nAgents = d?.campaign.n_low ?? 1
  const executed = d?.agents[0]?.n_slots
  const stoppedEarly = d?.is_frozen && seq && executed != null && executed < d.campaign.planned_slots
  const decided = allRows.some((r) => r.verdict != null)
  const sqlMain = v.sql.filter((s) => s.name.startsWith('verdicts') || s.name === 'wall_verdict')

  return (
    <div>
      <PageHeader title="Verdicts" lead="A two-step test with one error budget. Step 1 asks, per trading agent, whether it beats chance using ALL its slots (the gate). Only for agents that pass does step 2 ask where: which treatment cells, and which channel. Every claim on this page is covered by one family-wise error rate α."
        right={<select className="btn" value={id ?? ''} onChange={(e) => nav(`/verdicts/${e.target.value}`)} aria-label="Campaign" data-testid="campaign-select">
          {(campaigns.data ?? []).map((c) => <option key={c.campaign_id} value={c.campaign_id}>#{c.campaign_id} · {c.status} · {c.planned_slots} slots · {c.clock_mode === 'SIMULATED' ? 'simulated' : 'live'}{c.config?.inference === 'SEQUENTIAL' ? ' · sequential' : ''}</option>)}
          {!campaigns.data?.length && <option value="">no campaigns</option>}</select>} />
      {!id && !v.loading && !latest.loading ? <div className="card"><EmptyState title="No campaign yet">Run an audit from the Wall or the Run audit page; verdicts are frozen into audit_result when the campaign ends.</EmptyState></div> : (
        <div className="grid gap-5">
          {d && (
            <div className="card px-4 py-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-[0.88rem]" data-testid="campaign-meta">
              <span><span className="text-ink-3">campaign</span> <b className="num">#{d.campaign.campaign_id}</b></span>
              <span><span className="text-ink-3">wall</span> {d.campaign.wall_name}</span>
              <span><span className="text-ink-3">α (family-wise, every claim)</span> <b className="num">{d.campaign.alpha}</b></span>
              <span><span className="text-ink-3">slots</span> <b className="num">{fmtInt(executed ?? d.campaign.planned_slots)}</b>{stoppedEarly ? <> of {fmtInt(d.campaign.planned_slots)} planned</> : null} in {d.campaign.n_cells} cell{d.campaign.n_cells > 1 ? 's' : ''}</span>
              <Chip tone={seq ? 'steel' : 'plain'} title={seq ? 'Anytime-valid e-values: valid at every look and at any stopping time' : 'Exact tests, computed once at the planned n'}>{seq ? `sequential · ${d.campaign.config?.stop_rule?.toLowerCase().replace('_', ' ')}` : 'fixed n'}</Chip>
              {stoppedEarly && <Chip tone="steel" title="The pre-registered stopping rule fired at a block boundary">stopped early</Chip>}
              {d.campaign.clock_mode === 'SIMULATED' ? <Chip tone="steel">simulated clock</Chip> : <Chip>live clock</Chip>}
              <Chip tone={d.campaign.status === 'CLOSED' ? 'solid' : 'plain'}>{d.campaign.status}</Chip>
              {d.no_trade.some((x) => x.n_no_trade > 0) && <Chip title="Slots with no net position count as wrong. In a LIVE run a late order (slow machine) lands here." className="gap-1">no-trade slots: {d.no_trade.filter((x) => x.n_no_trade > 0).map((x) => `${x.low_agent.replace('trader-', '')} ${x.n_no_trade}/${x.n_slots}`).join(' · ')}</Chip>}
              {d.is_frozen && <span className="flex items-center gap-1 text-ink-2" title="each row's result_hash is recomputed from its stored fields and the snapshot hash"><IconCheck size={14} className="text-steel" />row hashes {allOk ? 'verified' : <b className="text-leak">MISMATCH</b>}</span>}
              <span className="text-ink-3 ml-auto">{fmtTime(d.campaign.closed_at ?? d.campaign.started_at)}</span>
            </div>)}

          {d?.legacy_v1 && <Banner tone="plain">Legacy campaign frozen by v1: one Holm family over every cell × agent, no pooled gate, no channel tests, no signed evidence. Re-run it to get the v2 analysis.</Banner>}

          {d && !d.legacy_v1 && (
            <div className={cx('card px-4 py-3.5 flex items-center gap-4 flex-wrap', d.wall?.verdict === 'LEAK' && 'border-leak-line')} data-testid="wall-verdict" data-verdict={d.wall?.verdict ?? (decided ? 'LIVE' : 'WITHHELD')}>
              <VerdictChip verdict={d.wall?.verdict ?? (decided && seq ? (d.agents.some((a) => a.verdict === 'LEAK') ? 'LEAK' : 'NO_EVIDENCE') : null)} size="lg" />
              <div className="min-w-0">
                <div className="font-semibold text-[1.05rem]">{d.wall ? (d.wall.verdict === 'LEAK' ? `The wall leaks: ${d.wall.agents_flagged} of ${d.wall.agents} trading agent${d.wall.agents > 1 ? 's' : ''} beat chance` : 'No evidence that any trading agent benefits from the wall\'s inside information')
                  : decided ? 'Running, anytime-valid: this verdict may be read now and will stay valid whenever the campaign stops' : 'Withheld until the planned n (fixed-n tests are invalid under peeking)'}</div>
                <div className="text-[0.85rem] text-ink-2">{d.wall ? <>wall p = <b className="num">{fmtP(d.wall.p_adj)}</b> (the smallest agent p after Bonferroni over {d.wall.agents} agents)</> : `${nAgents} agent${nAgents > 1 ? 's' : ''} × (1 pooled + cells + channels) hypotheses`}</div>
              </div>
            </div>)}

          <Panel title="Step 1 · The gate: does each agent beat chance?" loading={v.loading && !d} error={v.error} onRetry={v.reload} sql={sqlMain}
            subtitle={<>One test per agent, pooling all its slots (most power). Level α/{nAgents} each, so the agents together spend α. {seq ? 'Sequential: p = 1/E from a beta-binomial mixture e-value; valid at any stopping time.' : 'Exact one-sided binomial test; interval = Clopper–Pearson at the same level.'}</>}>
            {d && (
              <div className="overflow-auto">
                <table className="t num" data-testid="agent-table">
                  <thead><tr><th>agent</th><th className="r">n</th><th className="r">correct</th><th className="r">acc.</th><th title={seq ? 'anytime-valid confidence sequence' : 'two-sided Clopper–Pearson, each side at α/n_agents'}>{seq ? 'confidence sequence' : 'interval'}</th><th className="r">{seq ? 'p = 1/E' : 'p raw'}</th><th className="r">p adj. (×{nAgents})</th><th className="r" title="leakage in bits per decision: lower · upper bound">bits [lo · hi]</th><th>verdict</th></tr></thead>
                  <tbody>
                    {d.agents.map((r) => (
                      <tr key={r.low_agent} data-verdict={r.verdict ?? 'WITHHELD'} data-agent={r.low_agent}>
                        <td className="font-medium">{r.low_agent}</td><td className="r">{r.n_slots}</td><td className="r">{r.n_correct}</td><td className="r">{fmtAcc(r.accuracy)}</td>
                        <td className="whitespace-nowrap"><span className="inline-flex items-center gap-1.5"><Interval lo={r.acc_lower} acc={r.accuracy} hi={r.acc_upper} materiality={d.campaign.config?.materiality} /><span className="text-[0.74rem] text-ink-3">{fmtAcc(r.acc_lower)}–{fmtAcc(r.acc_upper)}</span></span></td>
                        <PCell r={r} seq={seq} /><td className="r font-medium">{fmtP(pAdj(r))}</td>
                        <td className="r">{fmtBits(r.leakage_bits_lower)} · <b className={cx(r.verdict === 'LEAK' && 'text-leak')}>{fmtBits(r.leakage_bits_upper)}</b></td>
                        <td><VerdictChip verdict={r.verdict} size="sm" /></td>
                      </tr>))}
                    {d.agents.length === 0 && <tr><td colSpan={9}><EmptyState title={d.legacy_v1 ? 'Not available for v1 campaigns' : 'No scored slots yet'} /></td></tr>}
                  </tbody>
                </table>
                <div className="px-4 py-2.5 text-[0.82rem] text-ink-2 border-t border-line-2">Read the upper bound too: a <b>NO EVIDENCE</b> agent with upper bound 0.62 can be leaking at most enough for 62% accuracy ({fmtBits(bitsOf(0.62))} bits per decision). That is what “no evidence” is worth at this n.</div>
              </div>)}
          </Panel>

          <Panel title="Step 2 · Where? Treatment cells" sql={sqlMain}
            subtitle="Tested only for agents whose gate passed, with Holm over the agent's cells and channels at α/n_agents. p adj. is the global adjusted p: max(gate, Holm)."
            loading={v.loading && !d}>
            {d && (
              <div className="overflow-auto" style={{ maxHeight: 520 }}>
                <table className="t num" data-testid="verdict-table">
                  <thead><tr><th>agent</th><th>cell (V N C)</th><th className="r">n</th><th className="r">correct</th><th className="r">acc.</th><th>interval</th><th className="r">{seq ? 'p = 1/E' : 'p raw'}</th><th className="r">p adj.</th><th>verdict</th><th className="r" title="minimum detectable accuracy at 80% power (fixed n only)">MDA</th></tr></thead>
                  <tbody>
                    {d.cells.map((r, i) => (
                      <tr key={i} className={cx(i > 0 && d.cells[i - 1].low_agent !== r.low_agent && 'border-t-2 border-line', gate[r.low_agent] === false && 'opacity-60')} data-verdict={r.verdict ?? 'WITHHELD'} data-agent={r.low_agent}>
                        <td className="font-medium">{r.low_agent}</td><td className="mono" title={isCellRow(r) ? cellName(r) : ''}>{isCellRow(r) ? cellLabel(r) : '—'}</td>
                        <td className="r">{r.n_slots}</td><td className="r">{r.n_correct}</td><td className="r">{fmtAcc(r.accuracy)}</td>
                        <td><Interval lo={r.acc_lower} acc={r.accuracy} hi={r.acc_upper} width={110} /></td>
                        <PCell r={r} seq={seq} /><td className="r font-medium">{fmtP(pAdj(r))}</td>
                        <td><VerdictChip verdict={r.verdict} size="sm" /></td>
                        <td className="r">{r.verdict === 'NO_EVIDENCE' && 'min_detectable_acc' in r ? (r.min_detectable_acc != null ? fmtAcc(r.min_detectable_acc) : <span title="cannot reject at this n and level, even with every slot correct" className="text-ink-2">none</span>) : <span className="text-ink-3">—</span>}</td>
                      </tr>))}
                    {d.cells.length === 0 && <tr><td colSpan={10}><EmptyState title="No scored slots yet" /></td></tr>}
                  </tbody>
                </table>
              </div>)}
          </Panel>

          {d && d.channels.length > 0 && (
            <Panel title="Step 2 · Which channel? Matched-pair attribution" sql={sqlMain}
              subtitle="Within each randomised block, compare the two slots that differ ONLY in this channel. Pairs where the agent was right with the channel on and wrong with it off (or the reverse) are the evidence; under no effect each such pair is a fair coin. An exact sign test, valid because block order is random.">
              <div className="overflow-auto">
                <table className="t num" data-testid="channel-table">
                  <thead><tr><th>agent</th><th>channel</th><th className="r" title="pairs where on and off disagree">discordant pairs</th><th className="r">right only with it ON</th><th className="r">share</th><th className="r">{seq ? 'p = 1/E' : 'p raw'}</th><th className="r">p adj.</th><th>verdict</th></tr></thead>
                  <tbody>
                    {d.channels.map((r, i) => (
                      <tr key={i} className={cx(i > 0 && d.channels[i - 1].low_agent !== r.low_agent && 'border-t-2 border-line', gate[r.low_agent] === false && 'opacity-60')} data-verdict={r.verdict ?? 'WITHHELD'} data-channel={`${r.low_agent}:${r.channel}`}>
                        <td className="font-medium">{r.low_agent}</td><td>{CHANNEL_LABEL[r.channel ?? '']}</td><td className="r">{r.n_slots}</td><td className="r">{r.n_correct}</td>
                        <td className="r">{r.n_slots ? fmtAcc(r.n_correct / r.n_slots) : '—'}</td><PCell r={r} seq={seq} /><td className="r font-medium">{fmtP(pAdj(r))}</td><td><VerdictChip verdict={r.verdict} size="sm" /></td>
                      </tr>))}
                  </tbody>
                </table>
                <div className="px-4 py-2.5 text-[0.82rem] text-ink-2 border-t border-line-2">An agent that reads several channels redundantly (trader-leaky) still leaks when one is switched off, unless the other two are off as well: only 1 of the 4 pairs per block shows its effect, so its channel tests are real effects with little power. A one-channel agent (trader-partial) disagrees on almost every pair of its channel.</div>
              </div>
            </Panel>)}

          {d && agents.length > 0 && (
            <Panel title="The factorial at a glance" sql={v.sql.filter((s) => s.name === 'channel_effect')} bodyClass="p-4"
              subtitle="Each tile is one treatment cell. Outlined = LEAK after gatekeeping. Bars: pooled accuracy with the channel on minus off (descriptive).">
              <div className="grid gap-3.5" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }}>
                {agents.map((a) => <Grid key={a} agent={a} rows={cellsByAgent[a]} effects={d.channel_effect} full={full} gate={gate[a] ?? null} />)}
              </div>
            </Panel>)}

          {d?.is_frozen && !d.legacy_v1 && id && <EvidencePanel cid={id} />}

          {d && (
            <div className="grid gap-5" style={{ gridTemplateColumns: 'minmax(0, 3fr) minmax(0, 2fr)' }}>
              <Panel title="Gateway accesses in this campaign" sql={v.sql.filter((s) => s.name === 'access_summary')} subtitle="Every read and write went through the logged gateway, each under the agent's own database login. Switched-off channels and missing grants are logged as DENIED." bodyClass="max-h-[300px] overflow-auto">
                <table className="t num"><thead><tr><th>asset</th><th>op</th><th>outcome</th><th className="r">events</th></tr></thead><tbody>
                  {d.access_summary.map((a, i) => <tr key={i}><td className="mono">{a.asset_name}</td><td>{a.op}</td><td>{a.outcome === 'DENIED' ? <span className="text-ink-2">✕ denied</span> : 'allowed'}</td><td className="r">{fmtInt(a.n)}</td></tr>)}
                  {d.access_summary.length === 0 && <tr><td colSpan={4} className="text-ink-3 p-4">No events.</td></tr>}</tbody></table>
              </Panel>
              <Panel title="How to read this" bodyClass="p-4 text-[0.9rem] text-ink-2 space-y-2.5">
                <p><b className="text-ink">One error budget.</b> The chance of ANY false LEAK on this page, agent, cell or channel, is at most α = {d.campaign.alpha}: each agent's gate spends α/{nAgents}, and its cells and channels reuse that share only if the gate opened.</p>
                {seq ? <p><b className="text-ink">Watching is allowed.</b> E-values are valid at every slot and whenever the campaign stops; the price is a slightly larger p than a fixed-n test on the same data (see Power → peeking).</p>
                  : <p><b className="text-ink">No peeking.</b> Fixed-n tests are computed once, at the planned n. <IconLock size={12} className="inline" /> Live charts elsewhere are descriptive.</p>}
                <p><b className="text-ink">Two-sided bounds.</b> The lower bound says how much leaks at least; the upper bound says how much could leak at most.</p>
                <p><b className="text-ink">Signed.</b> The frozen rows and a Merkle root of every logged row are signed; verify it above. <Link className="underline" to={`/inspector/${d.campaign.campaign_id}`}>Per-slot commitments →</Link></p>
              </Panel>
            </div>)}
        </div>)}
    </div>
  )
}
