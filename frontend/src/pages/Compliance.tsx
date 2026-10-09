import { useEffect, useMemo, useState } from 'react'
import { Banner, Chip, EmptyState, IconCheck, PageHeader, Panel } from '../components/ui'
import { Campaign, SqlMeta, api, useApi } from '../lib/api'
import { cx, fmtClock, fmtTime } from '../lib/format'

const TABS = [
  { id: 'sdd', label: 'SDD report' }, { id: 'grants', label: 'Grant review' }, { id: 'exposure', label: 'Exposure trail' },
  { id: 'search', label: 'Paraphrase search' }, { id: 'lag', label: 'Lag analysis' }, { id: 'models', label: 'Model comparison' },
]

function Search() {
  const [text, setText] = useState('Company will report quarterly profit well above consensus estimates')
  const [k, setK] = useState(10)
  const [res, setRes] = useState<{ rows: any[]; sql: SqlMeta[]; ms: number } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  async function run() {
    setBusy(true); setErr(null)
    const t = performance.now()
    try {
      const r = await api<{ data: { rows: any[] }; sql: SqlMeta[] }>('/api/compliance/semantic-search', { method: 'POST', body: JSON.stringify({ text, k }) })
      setRes({ rows: r.data.rows, sql: r.sql, ms: performance.now() - t })
    } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <Panel title="Which notes in shared memory paraphrase this text?" sql={res?.sql}
      subtitle="Embeds the text with the same model the agents use and finds the nearest notes in vector memory. EXACT: identical to scanning every note, computed over distinct vectors only (most notes repeat), so it stays fast at 200k notes."
      bodyClass="p-4 space-y-3">
      <div className="flex gap-2 items-start">
        <textarea className="btn flex-1 min-h-[64px] text-left" value={text} onChange={(e) => setText(e.target.value)} aria-label="Text to search for" data-testid="search-text" />
        <div className="flex flex-col gap-2">
          <select className="btn btn-sm" value={k} onChange={(e) => setK(Number(e.target.value))} aria-label="Results">{[5, 10, 25, 50].map((x) => <option key={x} value={x}>top {x}</option>)}</select>
          <button className="btn btn-primary btn-sm" onClick={run} disabled={busy || text.trim().length < 3} data-testid="search-run">Search</button>
        </div>
      </div>
      {err && <Banner tone="leak">{err}</Banner>}
      {res && (res.rows.length === 0 ? <EmptyState title="No embedded notes yet">Run an audit: the research agent writes notes into vector memory.</EmptyState> : (
        <div className="overflow-auto" style={{ maxHeight: 460 }}>
          <div className="text-[0.8rem] text-ink-3 mb-1.5 num">{res.rows.length} notes · {res.ms.toFixed(0)} ms round trip</div>
          <table className="t" data-testid="search-results"><thead><tr><th className="r">similarity</th><th>author</th><th>channel</th><th>security</th><th>note</th><th>written</th></tr></thead><tbody>
            {res.rows.map((r) => <tr key={r.note_id}><td className="r num font-medium">{r.similarity.toFixed(3)}</td><td>{r.author}</td><td className="mono text-[0.8rem]">{r.asset}</td><td className="mono text-[0.8rem]">{r.isin}</td><td className="text-ink-2">{r.body}</td><td className="num text-ink-3 text-[0.78rem]">{fmtTime(r.created_at)}</td></tr>)}
          </tbody></table></div>))}
    </Panel>)
}

function Sdd() {
  const [u, setU] = useState<string>('')
  const d = useApi<{ rows: any[]; items: any[] }>(`/api/compliance/sdd${u ? `?upsi_id=${u}` : ''}`, [u])
  return (
    <Panel title="Structured Digital Database: who received which UPSI item" subtitle="For a UPSI item: every person or agent it was shared with, by whom and when. UPSI text itself is not exposed to compliance." sql={d.sql} loading={d.loading && !d.data} error={d.error} onRetry={d.reload}
      actions={<select className="btn btn-sm" value={u} onChange={(e) => setU(e.target.value)} aria-label="UPSI item"><option value="">all UPSI items</option>{(d.data?.items ?? []).map((i) => <option key={i.upsi_id} value={i.upsi_id}>#{i.upsi_id} · {i.symbol} · {i.category}</option>)}</select>}>
      <div className="overflow-auto" style={{ maxHeight: 520 }}><table className="t" data-testid="sdd-table"><thead><tr><th>UPSI</th><th>security</th><th>category</th><th>shared by</th><th>recipient</th><th>purpose</th><th>shared at</th></tr></thead><tbody>
        {(d.data?.rows ?? []).map((r) => <tr key={r.sdd_id}><td className="num">#{r.upsi_id}</td><td className="mono">{r.symbol}</td><td>{r.category}</td><td>{r.shared_by} <Chip>{r.shared_by_kind}</Chip></td><td>{r.recipient} <Chip>{r.recipient_kind}</Chip></td><td className="text-ink-2">{r.purpose}</td><td className="num text-ink-3">{fmtTime(r.shared_at)}</td></tr>)}
        {d.data && d.data.rows.length === 0 && <tr><td colSpan={7}><EmptyState title="No sharing events" /></td></tr>}</tbody></table></div>
      <div className="px-4 py-3 text-[0.84rem] text-ink-2 border-t border-line-2">Note what is <i>absent</i>: no SDD row names a trading agent, yet a leak can still reach one through a shared channel. That gap is exactly what the canary audit measures.</div>
    </Panel>)
}

function Grants() {
  const d = useApi<{ rows: any[]; ok: boolean }>('/api/compliance/grant-review')
  return (
    <Panel title="Grant review: LOW-side agents holding any grant on an inside-owned asset" subtitle="Must return zero rows." sql={d.sql} loading={d.loading && !d.data} error={d.error} onRetry={d.reload}
      actions={d.data ? (d.data.ok ? <Chip tone="steel" className="gap-1"><IconCheck size={12} />0 rows</Chip> : <Chip tone="leak">{d.data.rows.length} rows</Chip>) : null}>
      {d.data && (d.data.ok ? <div data-testid="grant-review-ok"><EmptyState title="Zero rows: the wall holds at the access-control layer" icon={<IconCheck size={28} />}>No trading agent holds a grant on an asset owned by an inside area. The shared channels are owned by the (public) Technology Platform. Access control looks clean while information can still flow through them: that is why the audit exists.</EmptyState></div> : (
        <table className="t"><thead><tr><th>agent</th><th>wall</th><th>asset</th><th>owner dept</th><th>privilege</th></tr></thead><tbody>{d.data.rows.map((r) => <tr key={r.grant_id}><td>{r.agent_name}</td><td>{r.wall_name}</td><td className="mono">{r.asset_name}</td><td>{r.owner_dept}</td><td>{r.privilege}</td></tr>)}</tbody></table>))}
    </Panel>)
}

function Exposure() {
  const campaigns = useApi<Campaign[]>('/api/campaigns')
  const [cid, setCid] = useState<number | null>(null)
  const [sid, setSid] = useState<number | null>(null)
  useEffect(() => { if (!cid && campaigns.data?.length) setCid(campaigns.data.find((c) => c.slots_committed)?.campaign_id ?? null) }, [campaigns.data, cid])
  const slots = useApi<any[]>(cid ? `/api/campaigns/${cid}/slots?limit=2000` : null, [cid])
  useEffect(() => { if (slots.data?.length) setSid(slots.data[0].slot_id) }, [slots.data])
  const t = useApi<any[]>(sid ? `/api/compliance/exposure/${sid}` : null, [sid])
  return (
    <Panel title="Exposure trail for a canary" subtitle="Which agents touched rows derived from this canary, in time order (microsecond clock)." sql={t.sql} loading={t.loading && !t.data} error={t.error} onRetry={t.reload}
      actions={<div className="flex gap-2"><select className="btn btn-sm" value={cid ?? ''} onChange={(e) => setCid(Number(e.target.value))} aria-label="Campaign">{(campaigns.data ?? []).filter((c) => c.slots_committed).map((c) => <option key={c.campaign_id} value={c.campaign_id}>campaign #{c.campaign_id}</option>)}</select>
        <select className="btn btn-sm" value={sid ?? ''} onChange={(e) => setSid(Number(e.target.value))} aria-label="Slot">{(slots.data ?? []).slice(0, 400).map((s) => <option key={s.slot_id} value={s.slot_id}>slot {s.idx + 1}</option>)}</select></div>}>
      {!sid ? <EmptyState title="No slots yet">Run an audit to generate canaries.</EmptyState> : (
        <div className="overflow-auto" style={{ maxHeight: 520 }}><table className="t num" data-testid="exposure-table"><thead><tr><th>time (UTC)</th><th>agent</th><th>side</th><th>action</th><th>object</th><th>outcome</th></tr></thead><tbody>
          {(t.data ?? []).map((r, i) => <tr key={i}><td className="mono text-[0.8rem]">{fmtClock(r.event_time)}</td><td>{r.agent_name}</td><td><Chip tone={r.side === 'HIGH' ? 'solid' : 'plain'}>{r.side}</Chip></td><td className={cx(r.outcome === 'DENIED' && 'text-ink-3')}>{r.action}</td><td className="mono text-[0.8rem] text-ink-2">{r.object}</td><td>{r.outcome === 'DENIED' ? '✕ denied' : '✓'}</td></tr>)}</tbody></table></div>)}
    </Panel>)
}

function Lag() {
  const campaigns = useApi<Campaign[]>('/api/campaigns')
  const [cid, setCid] = useState<number | null>(null)
  useEffect(() => { if (!cid && campaigns.data?.length) setCid(campaigns.data.find((c) => c.slots_committed)?.campaign_id ?? null) }, [campaigns.data, cid])
  const d = useApi<{ summary: any[]; by_cell: any[] }>(cid ? `/api/compliance/lag/${cid}` : null, [cid])
  return (
    <Panel title="Lag analysis" subtitle="Time from the research agent reading a canary to each trading agent's first order in that security." sql={d.sql} loading={d.loading && !d.data} error={d.error} onRetry={d.reload}
      actions={<select className="btn btn-sm" value={cid ?? ''} onChange={(e) => setCid(Number(e.target.value))} aria-label="Campaign">{(campaigns.data ?? []).filter((c) => c.slots_committed).map((c) => <option key={c.campaign_id} value={c.campaign_id}>campaign #{c.campaign_id}{c.clock_mode === 'SIMULATED' ? ' (simulated clock)' : ''}</option>)}</select>}>
      {!cid || !d.data ? <EmptyState title="No data yet" /> : (
        <div className="p-4 space-y-4">
          {campaigns.data?.find((c) => c.campaign_id === cid)?.clock_mode === 'SIMULATED' && <Banner tone="steel">Simulated clock: these lags are the engine's scheduled offsets, not measured latencies. Run a LIVE campaign for real lags.</Banner>}
          <table className="t num"><thead><tr><th>agent</th><th className="r">slots</th><th className="r">mean (ms)</th><th className="r">median (ms)</th><th className="r">p90 (ms)</th></tr></thead><tbody>
            {d.data.summary.map((r) => <tr key={r.low_agent}><td>{r.low_agent}</td><td className="r">{r.n_slots}</td><td className="r">{r.mean_ms}</td><td className="r">{r.median_ms}</td><td className="r">{r.p90_ms}</td></tr>)}</tbody></table>
          <div className="eyebrow">median lag by treatment cell (V N C)</div>
          <div className="overflow-auto" style={{ maxHeight: 260 }}><table className="t num"><thead><tr><th>agent</th><th>cell</th><th className="r">slots</th><th className="r">median (ms)</th></tr></thead><tbody>
            {d.data.by_cell.map((r, i) => <tr key={i}><td>{r.low_agent}</td><td className="mono">{[r.vector_memory_on, r.notes_table_on, r.cache_on].map((x: boolean) => (x ? '1' : '0')).join(' ')}</td><td className="r">{r.n}</td><td className="r">{r.median_ms}</td></tr>)}</tbody></table></div>
        </div>)}
    </Panel>)
}

function Models() {
  const d = useApi<any[]>('/api/compliance/model-comparison')
  return (
    <Panel title="Model comparison" subtitle="Leak rate by agent model version across frozen campaigns." sql={d.sql} loading={d.loading && !d.data} error={d.error} onRetry={d.reload}>
      {d.data && d.data.length === 0 ? <EmptyState title="No frozen results yet" /> : (
        <table className="t num"><thead><tr><th>model</th><th>version</th><th className="r">results</th><th className="r">LEAK</th><th className="r">leak rate</th><th className="r">mean accuracy</th><th className="r">mean bits</th></tr></thead><tbody>
          {(d.data ?? []).map((r) => <tr key={r.model_name + r.model_version}><td>{r.model_name}</td><td>{r.model_version}</td><td className="r">{r.n_results}</td><td className="r">{r.n_leak}</td><td className="r">{((r.n_leak / r.n_results) * 100).toFixed(0)}%</td><td className="r">{r.mean_accuracy?.toFixed(3)}</td><td className="r">{r.mean_leakage_bits?.toFixed(3)}</td></tr>)}</tbody></table>)}
    </Panel>)
}

export default function Compliance() {
  const [tab, setTab] = useState('sdd')
  return (
    <div>
      <PageHeader title="Compliance" lead="SDD-style access history, grant review, exposure trail for a canary, paraphrase search over shared memory, lag analysis and model comparison, all answered from the same tables." />
      <div role="tablist" className="flex gap-1 mb-4 border-b border-line">
        {TABS.map((t) => <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)} data-testid={`tab-${t.id}`}
          className={cx('px-3.5 py-2 text-[0.93rem] font-medium -mb-px border-b-2', tab === t.id ? 'border-ink text-ink' : 'border-transparent text-ink-3 hover:text-ink')}>{t.label}</button>)}
      </div>
      {tab === 'sdd' && <Sdd />}{tab === 'grants' && <Grants />}{tab === 'exposure' && <Exposure />}{tab === 'search' && <Search />}{tab === 'lag' && <Lag />}{tab === 'models' && <Models />}
    </div>
  )
}
