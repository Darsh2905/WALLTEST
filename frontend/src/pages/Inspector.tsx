import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Banner, Chip, EmptyState, IconCheck, IconLock, IconX, PageHeader, Panel } from '../components/ui'
import { Campaign, useApi } from '../lib/api'
import { EvidencePanel } from '../components/EvidencePanel'
import { commitmentOf, flipOneBitHex, parseIsoMicros, preimage, sha256Hex } from '../lib/commitment'
import { cx, fmtClock, shortHash } from '../lib/format'

type Tamper = 'none' | 'flip' | 'salt' | 'start'
const TAMPER_LABEL: Record<Tamper, string> = { none: 'untouched', flip: 'flip bit inverted', salt: 'one salt bit changed', start: 'start shifted by 1 µs' }

function microsBetween(a: string, b: string): number { return Number(parseIsoMicros(b) - parseIsoMicros(a)) / 1000 }

function Detail({ sid }: { sid: number }) {
  const d = useApi<any>(`/api/slots/${sid}`, [sid])
  const [tamper, setTamper] = useState<Tamper>('none')
  const [browser, setBrowser] = useState<string | null>(null)
  const [pre, setPre] = useState<string>('')
  useEffect(() => setTamper('none'), [sid])
  const slot = d.data?.slot, rev = d.data?.reveal
  useEffect(() => {
    if (!slot || !rev) { setBrowser(null); setPre(''); return }
    let live = true
    const startIso: string = tamper === 'start' ? shiftMicro(slot.slot_start, 1) : slot.slot_start
    const flip: number = tamper === 'flip' ? 1 - rev.flip_bit : rev.flip_bit
    const salt: string = tamper === 'salt' ? flipOneBitHex(rev.salt_hex) : rev.salt_hex
    const p = preimage(slot.campaign_id, parseIsoMicros(startIso), flip, salt)
    setPre(p)
    sha256Hex(p).then((h) => { if (live) setBrowser(h) })
    return () => { live = false }
  }, [slot, rev, tamper])

  if (!slot) return <Panel title={`Slot ${sid}`} loading={d.loading} error={d.error} onRetry={d.reload} sql={d.sql}><div /></Panel>
  const match = browser != null && slot.commitment === browser
  const lead = microsBetween(slot.committed_at, slot.slot_start)
  const dur = microsBetween(slot.slot_start, slot.slot_end)
  const parts = pre ? pre.split('|') : []

  return (
    <Panel title={`Slot ${sid}`} subtitle="Row-level security: the flip is readable only after the slot has ended." sql={d.sql}>
    <div className="p-4 space-y-4" data-testid="slot-detail" data-slot={sid}>
      <div className="grid grid-cols-3 gap-3 text-[0.88rem]">
        {[['committed_at', slot.committed_at], ['slot opens', slot.slot_start], ['slot ends', slot.slot_end]].map(([k, v]) => (
          <div key={k} className="rounded-lg border border-line bg-surface-2 px-3 py-2"><div className="eyebrow">{k}</div><div className="mono num text-[0.82rem]">{fmtClock(v as string)}</div><div className="text-[0.72rem] text-ink-3">{(v as string).slice(0, 10)} UTC</div></div>))}
      </div>
      <div className="text-[0.88rem] text-ink-2">
        Commitment published <b className="num">{lead.toFixed(1)} ms</b> before the slot opened (slot length <b className="num">{dur.toFixed(0)} ms</b>).{' '}
        {lead > 0 ? <span className="inline-flex items-center gap-1 text-steel"><IconCheck size={13} />commit-before-expose holds</span> : <span className="text-leak">violated</span>}
      </div>

      <div>
        <div className="eyebrow mb-1">commitment published in canary_slot</div>
        <div className="mono text-[0.84rem] break-all rounded-lg border border-line bg-surface-2 p-2.5" data-testid="db-commitment">{slot.commitment}</div>
      </div>

      {!rev ? (
        <div className="rounded-lg border border-dashed border-line p-4 flex items-center gap-3 text-ink-2" data-testid="sealed-note">
          <IconLock size={20} /><div><b>Flip sealed.</b> Row-level security hides sealed_flip until this slot has ended; there is nothing to recompute yet. State: <b>{slot.state}</b>.</div>
        </div>
      ) : (
        <>
          <div>
            <div className="eyebrow mb-1">revealed after the slot ended: the preimage the browser hashes</div>
            <div className="grid gap-1.5 text-[0.84rem]" style={{ gridTemplateColumns: '120px 1fr' }}>
              <span className="text-ink-3">prefix</span><span className="mono">{parts[0]}</span>
              <span className="text-ink-3">campaign_id</span><span className="mono">{parts[1]}</span>
              <span className="text-ink-3">start_us</span><span className={cx('mono', tamper === 'start' && 'bg-leak-bg text-leak-ink')}>{parts[2]} <span className="text-ink-3">(µs since epoch, parsed in the browser from {slot.slot_start})</span></span>
              <span className="text-ink-3">flip_bit</span><span className={cx('mono', tamper === 'flip' && 'bg-leak-bg text-leak-ink')}>{parts[3]}</span>
              <span className="text-ink-3">salt</span><span className={cx('mono break-all', tamper === 'salt' && 'bg-leak-bg text-leak-ink')}>{parts[4]}</span>
            </div>
          </div>
          <div className={cx('rounded-lg border p-3', match ? 'border-steel-line bg-steel-bg' : 'border-leak-line bg-leak-bg')} data-testid="verify-result" data-match={match ? '1' : '0'}>
            <div className="flex items-center gap-2 font-semibold">
              {browser == null ? 'hashing…' : match ? <><IconCheck size={18} className="text-steel" /><span className="text-steel">MATCH</span> — SHA-256 recomputed in your browser equals the published commitment</> : <><IconX size={18} className="text-leak-ink" /><span className="text-leak-ink">MISMATCH</span> — this is NOT the committed value</>}
            </div>
            <div className="mono text-[0.8rem] break-all mt-1.5" data-testid="browser-hash">{browser ?? '…'}</div>
            <div className="text-[0.78rem] text-ink-3 mt-1">database also recomputed it: {rev.commitment_ok ? '✓ commitment_ok' : '✗ mismatch'} · inputs: {TAMPER_LABEL[tamper]}</div>
          </div>
          <div className="flex flex-wrap gap-2 items-center">
            <span className="eyebrow mr-1">tamper test</span>
            <button className="btn btn-sm" onClick={() => setTamper('flip')} data-testid="tamper-flip">Invert the flip</button>
            <button className="btn btn-sm" onClick={() => setTamper('salt')} data-testid="tamper-salt">Change one salt bit</button>
            <button className="btn btn-sm" onClick={() => setTamper('start')} data-testid="tamper-start">Shift start by 1 µs</button>
            <button className="btn btn-sm btn-ghost" onClick={() => setTamper('none')} disabled={tamper === 'none'}>Reset</button>
          </div>
          {d.data.scores?.length > 0 && (
            <div><div className="eyebrow mb-1">scoring of this slot (v_slot_score)</div>
              <table className="t num"><thead><tr><th>agent</th><th className="r">net position</th><th>guess</th><th>truth</th><th>correct</th></tr></thead><tbody>
                {d.data.scores.map((s: any) => <tr key={s.low_agent}><td>{s.low_agent}</td><td className="r">{s.net_position}</td><td>{s.guess_direction ?? 'no trade'}</td><td>{s.true_direction}</td><td>{s.correct ? '✓' : '✗'}</td></tr>)}</tbody></table></div>)}
        </>
      )}
    </div>
    </Panel>
  )
}

function shiftMicro(iso: string, delta: number): string {
  const us = parseIsoMicros(iso) + BigInt(delta)
  const secs = Number(us / 1_000_000n)
  const frac = String(us % 1_000_000n).padStart(6, '0')
  return new Date(secs * 1000).toISOString().slice(0, 19) + '.' + frac + 'Z'
}

export default function Inspector() {
  const { cid } = useParams()
  const [sp] = useSearchParams()
  const nav = useNavigate()
  const campaigns = useApi<Campaign[]>('/api/campaigns')
  const latest = useApi<{ campaign_id: number | null }>('/api/campaigns/latest')
  const id = cid ? Number(cid) : latest.data?.campaign_id ?? campaigns.data?.[0]?.campaign_id ?? null
  useEffect(() => { if (!cid && id) nav(`/inspector/${id}`, { replace: true }) }, [cid, id, nav])
  const slots = useApi<any[]>(id ? `/api/campaigns/${id}/slots?limit=5000` : null, [id])
  const reveals = useApi<any[]>(id ? `/api/campaigns/${id}/reveals` : null, [id])
  const [sel, setSel] = useState<number | null>(null)
  const [vstate, setV] = useState<{ done: number; ok: number; bad: number[]; running: boolean }>({ done: 0, ok: 0, bad: [], running: false })
  useEffect(() => { const q = sp.get('slot'); if (q) setSel(Number(q)) }, [sp])
  useEffect(() => { if (slots.data?.length && sel == null) setSel(slots.data[0].slot_id) }, [slots.data, sel])
  useEffect(() => setV({ done: 0, ok: 0, bad: [], running: false }), [id])

  async function verifyAll() {
    const rows = reveals.data ?? []
    setV({ done: 0, ok: 0, bad: [], running: true })
    let ok = 0; const bad: number[] = []
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i]
      const h = await commitmentOf(r.campaign_id, r.slot_start, r.flip_bit, r.salt_hex)
      if (h === r.commitment.trim()) ok++; else bad.push(r.slot_id)
      if (i % 10 === 0 || i === rows.length - 1) { setV({ done: i + 1, ok, bad: [...bad], running: i < rows.length - 1 }); await new Promise((res) => setTimeout(res, 0)) }
    }
  }
  const verified = vstate.done > 0
  const camp = campaigns.data?.find((c) => c.campaign_id === id)
  const revealed = useMemo(() => new Set((reveals.data ?? []).map((r) => r.slot_id)), [reveals.data])

  return (
    <div>
      <PageHeader title="Commit–reveal inspector" lead="For every slot the browser recomputes SHA-256 from the revealed flip, salt and the microsecond start time, and compares it with the commitment the database published before the slot opened. For a frozen campaign it also checks the signed verdict and the Merkle root over every logged row."
        right={<select className="btn" value={id ?? ''} onChange={(e) => nav(`/inspector/${e.target.value}`)} aria-label="Campaign">{(campaigns.data ?? []).map((c) => <option key={c.campaign_id} value={c.campaign_id}>#{c.campaign_id} · {c.planned_slots} slots · {c.clock_mode === 'SIMULATED' ? 'simulated' : 'live'}</option>)}</select>} />
      {camp?.clock_mode === 'SIMULATED' && <div className="mb-3"><Banner tone="steel"><b>Simulated clock.</b> This campaign's timestamps were back-dated by the engine. The hash check below is exactly as strong; what a simulated clock cannot prove is that the commitment preceded the slot on the real wall clock.</Banner></div>}
      {!id && !campaigns.loading ? <div className="card"><EmptyState title="No campaign yet">Run an audit first; each slot's commitment appears here as soon as it is published.</EmptyState></div> : (
        <div className="grid gap-5" style={{ gridTemplateColumns: 'minmax(0, 5fr) minmax(0, 7fr)' }}>
          <Panel title="Slots" subtitle={slots.data ? `${slots.data.length} committed · ${revealed.size} revealed` : ''} sql={[...slots.sql, ...reveals.sql]} loading={slots.loading && !slots.data} error={slots.error} onRetry={slots.reload}
            actions={<button className="btn btn-sm" onClick={verifyAll} disabled={!reveals.data?.length || vstate.running} data-testid="verify-all">Verify all in browser</button>}>
            {verified && (
              <div className={cx('mx-3 mt-3 rounded-lg border px-3 py-2 text-[0.88rem] flex items-center gap-2', vstate.bad.length ? 'border-leak-line bg-leak-bg text-leak-ink' : 'border-steel-line bg-steel-bg text-steel')} data-testid="verify-all-result">
                {vstate.bad.length ? <IconX size={16} /> : <IconCheck size={16} />}
                <span className="num"><b>{vstate.ok}</b> of <b>{reveals.data?.length}</b> commitments verified in your browser{vstate.bad.length ? ` · ${vstate.bad.length} FAILED` : ''}{vstate.running ? ' …' : ''}</span>
              </div>)}
            <div className="overflow-auto mt-2" style={{ maxHeight: 640 }} data-testid="slot-list">
              <table className="t num"><thead><tr><th>#</th><th>state</th><th>commitment</th><th>committed</th></tr></thead><tbody>
                {(slots.data ?? []).map((s) => (
                  <tr key={s.slot_id} onClick={() => setSel(s.slot_id)} className={cx('cursor-pointer', sel === s.slot_id && 'bg-steel-bg')} data-slot-row={s.slot_id}>
                    <td>{s.idx + 1}</td>
                    <td>{revealed.has(s.slot_id) ? <span className="inline-flex items-center gap-1"><IconCheck size={13} className="text-steel" />revealed</span> : s.state === 'OPEN' ? <Chip tone="steel">open</Chip> : <span className="inline-flex items-center gap-1 text-ink-3"><IconLock size={12} />sealed</span>}</td>
                    <td className="mono text-[0.8rem]">{shortHash(s.commitment, 12)}…</td>
                    <td className="mono text-[0.76rem] text-ink-3">{fmtClock(s.committed_at).slice(0, 15)}</td>
                  </tr>))}
              </tbody></table>
            </div>
          </Panel>
          {sel ? <Detail sid={sel} /> : <Panel title="Slot"><EmptyState title="Select a slot" /></Panel>}
          {id && camp?.status === 'CLOSED' && <div className="col-span-full"><EvidencePanel cid={id} /></div>}
        </div>)}
    </div>
  )
}
