import { useEffect, useState } from 'react'
import { Banner, Chip, IconCheck, PageHeader, Panel } from '../components/ui'
import { useApi } from '../lib/api'
import { cx, fmtAcc, fmtInt, fmtPct } from '../lib/format'

function useDebounced<T>(v: T, ms = 250): T {
  const [x, setX] = useState(v)
  useEffect(() => { const t = setTimeout(() => setX(v), ms); return () => clearTimeout(t) }, [v, ms])
  return x
}

function Curve({ curve, mda, acc, target }: { curve: { accuracy: number; power: number }[]; mda: number | null; acc: number; target: number }) {
  const w = 620, h = 300, m = { l: 46, r: 16, t: 14, b: 38 }
  const x = (a: number) => m.l + ((a - 0.5) / 0.45) * (w - m.l - m.r), y = (p: number) => m.t + (1 - p) * (h - m.t - m.b)
  const [hov, setHov] = useState<number | null>(null)
  const pt = hov != null ? curve.reduce((b, c) => (Math.abs(c.accuracy - hov) < Math.abs(b.accuracy - hov) ? c : b), curve[0]) : null
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full" style={{ maxHeight: 320 }} role="img" aria-label="Exact power versus true accuracy"
      onMouseMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); const px = ((e.clientX - r.left) / r.width) * w; setHov(0.5 + ((px - m.l) / (w - m.l - m.r)) * 0.45) }} onMouseLeave={() => setHov(null)}>
      {[0, 0.2, 0.4, 0.6, 0.8, 1].map((p) => <g key={p}><line x1={m.l} x2={w - m.r} y1={y(p)} y2={y(p)} stroke="var(--grid)" /><text x={m.l - 7} y={y(p)} dy=".32em" fontSize="11" textAnchor="end" fill="var(--ink-3)" className="num">{p}</text></g>)}
      {[0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95].map((a) => <text key={a} x={x(a)} y={h - 20} fontSize="11" textAnchor="middle" fill="var(--ink-3)" className="num">{a}</text>)}
      <text x={w / 2} y={h - 4} fontSize="11.5" textAnchor="middle" fill="var(--ink-3)">true accuracy of the trading agent</text>
      <text transform={`translate(12,${h / 2}) rotate(-90)`} fontSize="11.5" textAnchor="middle" fill="var(--ink-3)">power (exact binomial)</text>
      <line x1={m.l} x2={w - m.r} y1={y(target)} y2={y(target)} stroke="var(--ink-3)" strokeDasharray="4 3" />
      <text x={w - m.r - 2} y={y(target) - 5} fontSize="11" textAnchor="end" fill="var(--ink-2)">target power {target}</text>
      <path d={curve.map((c, i) => `${i ? 'L' : 'M'}${x(c.accuracy).toFixed(1)},${y(c.power).toFixed(1)}`).join('')} fill="none" stroke="var(--steel)" strokeWidth="2.2" />
      {mda && <g><line x1={x(mda)} x2={x(mda)} y1={m.t} y2={h - m.b} stroke="var(--ink)" strokeWidth="1.3" strokeDasharray="2 3" /><text x={x(mda) + 6} y={m.t + 12} fontSize="11.5" fontWeight="600" fill="var(--ink)">min detectable {mda.toFixed(3)}</text></g>}
      <line x1={x(acc)} x2={x(acc)} y1={m.t} y2={h - m.b} stroke="var(--ink-3)" strokeDasharray="1 3" /><text x={x(acc) + 5} y={h - m.b - 6} fontSize="11" fill="var(--ink-3)">planted {acc}</text>
      {pt && <g pointerEvents="none"><circle cx={x(pt.accuracy)} cy={y(pt.power)} r="4.5" fill="var(--steel)" stroke="var(--surface)" strokeWidth="2" /><text x={x(pt.accuracy)} y={y(pt.power) - 10} fontSize="11.5" textAnchor="middle" fill="var(--ink)" className="num">acc {pt.accuracy.toFixed(3)} → power {pt.power.toFixed(3)}</text></g>}
    </svg>
  )
}

export default function Power() {
  const [alpha, setAlpha] = useState(0.001)
  const [target, setTarget] = useState(0.8)
  const [accs, setAccs] = useState('0.70, 0.60, 0.55')
  const [n, setN] = useState(1500)
  const [pacc, setPacc] = useState(0.55)
  const q = useDebounced(`alpha=${alpha}&power=${target}&accs=${accs.replace(/\s/g, '')}`)
  const table = useApi<{ rows: any[]; alpha: number; power: number }>(`/api/power/table?${q}`, [q])
  const pq = useDebounced(`n=${n}&alpha=${alpha}&power=${target}&acc=${pacc}`)
  const point = useApi<{ point: any; curve: any[] }>(`/api/power/point?${pq}`, [pq])
  const rows = table.data?.rows ?? []
  const reproduces = alpha === 0.001 && target === 0.8 && rows.length > 0 && rows.every((r) => r.doc_n == null || r.doc_n === r.n_normal)

  return (
    <div>
      <PageHeader title="Power & sample size" lead="The proposal's sample-size table, recomputed in SQL. Its figures come from the normal approximation; the database also computes the exact binomial answer, which is slightly larger because the exact test is discrete." />
      <div className="grid gap-5">
        <Panel title="Sample size for a one-sided exact binomial test of accuracy > 0.5" sql={table.sql} loading={table.loading && !table.data} error={table.error} onRetry={table.reload} bodyClass="p-4"
          subtitle="Smallest n reaching the target power. Exact power is a sawtooth in n, so “exact n” is the first crossing."
          actions={reproduces ? <Chip tone="steel" className="gap-1"><IconCheck size={12} />reproduces the proposal's 94 / 384 / 1,543</Chip> : null}>
          <div className="flex flex-wrap items-end gap-5 mb-4">
            <label className="block"><span className="eyebrow">alpha (false-alarm rate)</span><select className="mt-1 btn block" value={alpha} onChange={(e) => setAlpha(Number(e.target.value))} data-testid="power-alpha">{[0.1, 0.05, 0.01, 0.001, 0.0001].map((a) => <option key={a} value={a}>{a}</option>)}</select></label>
            <label className="block w-56"><span className="flex justify-between"><span className="eyebrow">target power</span><span className="num text-[0.85rem]">{target.toFixed(2)}</span></span><input type="range" min={0.6} max={0.95} step={0.01} value={target} onChange={(e) => setTarget(Number(e.target.value))} className="w-full accent-[var(--steel)]" /></label>
            <label className="block flex-1 min-w-48"><span className="eyebrow">accuracies to test (comma-separated)</span><input className="mt-1 w-full btn mono" value={accs} onChange={(e) => setAccs(e.target.value)} data-testid="power-accs" /></label>
          </div>
          <table className="t num" data-testid="power-table"><thead><tr><th>true accuracy</th><th className="r">proposal n</th><th className="r">normal approx. n</th><th className="r">exact binomial n</th><th className="r">exact power at the normal-approx. n</th><th>note</th></tr></thead><tbody>
            {rows.map((r) => (
              <tr key={r.accuracy} data-acc={r.accuracy}>
                <td className="font-medium">{fmtPct(r.accuracy, 0)}</td><td className="r">{r.doc_n ? fmtInt(r.doc_n) : <span className="text-ink-3">—</span>}</td>
                <td className="r font-semibold">{fmtInt(r.n_normal)}{r.doc_n && r.doc_n === r.n_normal ? <IconCheck size={13} className="inline ml-1 text-steel" /> : null}</td>
                <td className="r font-semibold">{r.n_exact ? fmtInt(r.n_exact) : <span className="text-ink-3 font-normal">&gt; 3,000 (scan skipped)</span>}</td>
                <td className="r">{fmtPct(r.power_at_normal_n, 1)}</td>
                <td className="text-ink-2 text-[0.84rem]">{r.n_exact && r.n_exact > r.n_normal ? `exact needs +${r.n_exact - r.n_normal} slots (${(((r.n_exact - r.n_normal) / r.n_normal) * 100).toFixed(1)}%)` : r.n_exact === r.n_normal ? 'identical' : ''}</td>
              </tr>))}
          </tbody></table>
          {reproduces && <p className="text-[0.86rem] text-ink-2 mt-3">At α = 0.001 and 80% power the normal approximation gives exactly the proposal's 94, 384 and 1,543 slots. The exact binomial needs a few more, and the proposal's own n delivers 77–80% rather than a full 80% power.</p>}
        </Panel>

        <Panel title="Power curve and minimum detectable accuracy at a planned n" sql={point.sql} loading={point.loading && !point.data} error={point.error} onRetry={point.reload} bodyClass="p-4">
          <div className="grid gap-6" style={{ gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 3fr)' }}>
            <div className="space-y-4">
              <label className="block"><span className="flex justify-between"><span className="eyebrow">planned slots n</span><span className="num text-[0.85rem]">{fmtInt(n)}</span></span><input type="range" min={20} max={4000} step={10} value={n} onChange={(e) => setN(Number(e.target.value))} className="w-full accent-[var(--steel)]" data-testid="power-n" /></label>
              <label className="block"><span className="flex justify-between"><span className="eyebrow">planted accuracy</span><span className="num text-[0.85rem]">{pacc.toFixed(3)}</span></span><input type="range" min={0.505} max={0.95} step={0.005} value={pacc} onChange={(e) => setPacc(Number(e.target.value))} className="w-full accent-[var(--steel)]" /></label>
              {point.data && (
                <dl className="grid grid-cols-2 gap-3 text-[0.9rem]" data-testid="power-point">
                  <div><dt className="eyebrow">critical correct count</dt><dd className="num font-semibold text-[1.2rem]">{fmtInt(point.data.point.critical_k)} / {fmtInt(n)}</dd><div className="text-[0.76rem] text-ink-3">reject H₀ at or above this</div></div>
                  <div><dt className="eyebrow">min detectable accuracy</dt><dd className="num font-semibold text-[1.2rem]">{point.data.point.min_detectable_acc ? fmtAcc(point.data.point.min_detectable_acc) : '—'}</dd><div className="text-[0.76rem] text-ink-3">at {fmtPct(target, 0)} power</div></div>
                  <div><dt className="eyebrow">power at planted accuracy</dt><dd className="num font-semibold text-[1.2rem]">{fmtPct(point.data.point.power_at_acc, 1)}</dd></div>
                  <div><dt className="eyebrow">leakage at planted accuracy</dt><dd className="num font-semibold text-[1.2rem]">{point.data.point.bits_at_acc.toFixed(4)} bits</dd><div className="text-[0.76rem] text-ink-3">1 − H(a) per decision</div></div>
                </dl>)}
              <Banner tone="plain">“No evidence” is not “proven clean”: with this n the test cannot see leaks weaker than the minimum detectable accuracy.</Banner>
            </div>
            <div>{point.data ? <Curve curve={point.data.curve} mda={point.data.point.min_detectable_acc} acc={pacc} target={target} /> : <div className="skeleton" style={{ height: 300 }} />}</div>
          </div>
        </Panel>
      </div>
    </div>
  )
}
