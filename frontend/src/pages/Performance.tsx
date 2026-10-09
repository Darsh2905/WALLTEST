import { Banner, Chip, EmptyState, PageHeader, Panel } from '../components/ui'
import { useApi } from '../lib/api'
import { cx, fmtInt } from '../lib/format'

type B = Record<string, any>

const LABEL: Record<string, string> = {
  v_verdict_one_campaign_ms: 'verdict of one campaign (v_verdict)', v_slot_score_one_slot_ms: 'score one slot', v_progress_one_campaign_ms: 'progress of one campaign',
  exposure_trail_ms: 'exposure trail of one canary', v_lag_one_campaign_ms: 'lag analysis of one campaign', access_event_time_window_count_ms: 'access events in a campaign window',
  knn_all_vector_memory_ms: 'raw k-NN over all vector memory (no index)', v_wall_verdict_one_campaign_ms: 'wall verdict', evidence_root_one_campaign_ms: 'evidence Merkle root',
  semantic_search_ms: 'semantic search (exact, distinct vectors)', semantic_search_bruteforce_ms: 'semantic search (brute force)',
  binom_upper_p_1543_ms: 'exact binomial p (n = 1,543)', clopper_pearson_lower_1543_ms: 'Clopper–Pearson lower (n = 1,543)', clopper_pearson_upper_1543_ms: 'Clopper–Pearson upper (n = 1,543)',
  min_detectable_acc_1500_ms: 'minimum detectable accuracy (n = 1,500)', n_required_exact_055_ms: 'exact sample size for 55%', cs_lower_1543_ms: 'confidence sequence lower (n = 1,543)',
  log_evalue_mix_1543_ms: 'mixture e-value (n = 1,543)',
}

/** log-ratio bar: how many times faster (right) or slower (left) v2 is; 1x in the middle. Within ±15% is measurement noise. */
function Speed({ v1, v2 }: { v1: number | undefined; v2: number }) {
  if (v1 == null) return <span className="text-ink-3 text-[0.8rem]">new in v2</span>
  const r = v1 / v2, w = 150, mid = w / 2, s = Math.max(-1, Math.min(1, Math.log10(r) / 3)) * (mid - 2)
  const same = r > 1 / 1.15 && r < 1.15
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap">
      <svg width={w} height="12" role="img" aria-label={same ? 'about the same' : `${r.toFixed(1)} times ${r >= 1 ? 'faster' : 'slower'}`}>
        <line x1={mid} x2={mid} y1="0" y2="12" stroke="var(--ink-3)" />
        {!same && <rect x={s >= 0 ? mid : mid + s} width={Math.max(1, Math.abs(s))} y="2" height="8" rx="2" fill={r >= 1 ? 'var(--steel)' : 'var(--leak)'} />}
      </svg>
      <span className={cx('num text-[0.82rem]', same ? 'text-ink-3' : r < 1 && 'text-leak-ink')}>{same ? '≈ same' : r >= 1 ? `${r >= 10 ? r.toFixed(0) : r.toFixed(1)}× faster` : `${(1 / r).toFixed(1)}× slower`}</span>
    </span>)
}

function LatencyTable({ v1, v2, keys }: { v1: B; v2: B; keys: string[] }) {
  return (
    <table className="t num"><thead><tr><th>operation</th><th className="r">v1 ms</th><th className="r">v2 ms</th><th>speed-up (log scale, ±1000×)</th></tr></thead><tbody>
      {keys.map((k) => (
        <tr key={k} data-metric={k}><td>{LABEL[k] ?? k}</td><td className="r text-ink-3">{v1[k] ?? '—'}</td><td className="r font-medium">{v2[k]}</td><td><Speed v1={v1[k]} v2={v2[k]} /></td></tr>))}
    </tbody></table>)
}

function Range({ r, max }: { r: { median: number; min: number; max: number }; max: number }) {
  const x = (v: number) => (v / max) * 160
  return (
    <svg width="230" height="16" role="img" aria-label={`median ${r.median}, range ${r.min} to ${r.max}`}>
      <line x1={x(r.min)} x2={x(r.max)} y1="8" y2="8" stroke="var(--ink-3)" strokeWidth="2" />
      <line x1={x(r.min)} x2={x(r.min)} y1="4" y2="12" stroke="var(--ink-3)" strokeWidth="2" /><line x1={x(r.max)} x2={x(r.max)} y1="4" y2="12" stroke="var(--ink-3)" strokeWidth="2" />
      <circle cx={x(r.median)} cy="8" r="4.5" fill="var(--ink)" stroke="var(--surface)" strokeWidth="2" />
      <text x={Math.min(x(r.max) + 8, 175)} y="12" fontSize="10.5" fill="var(--ink-3)" className="num">{r.min.toFixed(0)}–{r.max.toFixed(0)}</text>
    </svg>)
}

export default function Performance() {
  const b = useApi<{ benchmarks: Record<string, B> }>('/api/benchmarks')
  const bm = b.data?.benchmarks ?? {}
  const v1 = bm['v1-baseline'], v2 = bm['v2'], ab = bm['ab-engine-v1-v2'], st = bm['index-studies']
  const qKeys = v2 ? Object.keys(v2.queries ?? {}).filter((k) => k !== 'rows' && typeof v2.queries[k] === 'number') : []
  const kKeys = v2 ? Object.keys(v2.kernels ?? {}) : []
  const abMax = ab ? Math.max(...Object.values<any>(ab.results).map((r) => r.max)) * 1.1 : 1
  return (
    <div>
      <PageHeader title="Performance" lead="What v2 changed, measured on the same server: an interleaved A/B of the engine, query latency on the calibration database (about a million logged events), the statistical kernels, and two index designs that were tried and rejected. Every number is read from docs/benchmarks/, written by the scripts named on each panel." />
      {b.loading && !b.data ? <div className="card"><div className="skeleton m-4" style={{ height: 200 }} /></div> : !v2 ? <div className="card"><EmptyState title="No benchmark results">Run scripts/bench.py --label v2.</EmptyState></div> : (
        <div className="grid gap-5">
          {ab && (
            <Panel title="Engine throughput: v1 vs v2, interleaved A/B" bodyClass="p-4 space-y-3" subtitle={`${ab.metric}. Fresh database per run, ${ab.results.baseline_conc1.runs.length} runs per cell, baseline = ${ab.baseline_ref}. scripts/ab_engine.py`}>
              <table className="t num" data-testid="ab-table"><thead><tr><th>concurrency</th><th>version</th><th className="r">median</th><th>min · median · max (slots/s)</th><th className="r">runs</th></tr></thead><tbody>
                {(['conc1', 'conc8'] as const).flatMap((c) => (['baseline', 'working_tree'] as const).map((v) => {
                  const r = ab.results[`${v}_${c}`]
                  return <tr key={v + c}><td>{c === 'conc1' ? '1 slot at a time' : '8 slots in flight'}</td><td>{v === 'baseline' ? 'v1' : 'v2'}</td><td className="r font-semibold">{r.median.toFixed(1)}</td><td><Range r={r} max={abMax} /></td><td className="r text-ink-3 text-[0.78rem]">{r.runs.join(' · ')}</td></tr>
                }))}
              </tbody></table>
              <div className="text-[0.86rem] text-ink-2">v2 does more work per slot (every agent logs in as itself; every row feeds a signed Merkle root) and is still faster: <b className="num">{((ab.results.working_tree_conc1.median / ab.results.baseline_conc1.median - 1) * 100).toFixed(0)}%</b> one slot at a time, <b className="num">{((ab.results.working_tree_conc8.median / ab.results.baseline_conc8.median - 1) * 100).toFixed(0)}%</b> with eight in flight (the ranges overlap there). The largest single gain came from profiling: one gateway read was 42% of all database time.</div>
            </Panel>)}

          <Panel title="Query latency at scale" bodyClass="overflow-auto" subtitle={<>Server-side execution time (EXPLAIN ANALYZE, median of 5) as the audit_engine role, on {fmtInt(v2.queries.rows?.access_event)} access events, {fmtInt(v2.queries.rows?.trade_order)} orders and {fmtInt(v2.queries.rows?.agent_note)} notes. scripts/bench.py</>}>
            <LatencyTable v1={v1?.queries ?? {}} v2={v2.queries} keys={qKeys.filter((k) => !k.startsWith('semantic_search_equals') && k !== 'knn_all_vector_memory_ms')} />
            <div className="px-4 py-2 text-[0.84rem] text-ink-2 border-t border-line-2">{v2.queries.semantic_search_equals_bruteforce && <>Semantic search returned exactly the brute-force rows on <b className="num">{v2.queries.semantic_search_equals_bruteforce}</b> canary texts. </>}
              Not listed: v1's raw k-NN over all of vector memory ({v1?.queries?.knn_all_vector_memory_ms} ms, no index), which v2 never runs; semantic search replaced it.</div>
          </Panel>

          <Panel title="Statistical kernels" bodyClass="overflow-auto" subtitle="Bounds and power are Beta quantiles. v2 solves them with a log-space regularised incomplete beta (Lentz continued fraction) and safeguarded Newton, instead of bisecting binomial sums. Accuracy is checked against 60-digit mpmath in the test suite.">
            <LatencyTable v1={v1?.kernels ?? {}} v2={v2.kernels} keys={kKeys} />
          </Panel>

          {st && (
            <div className="grid gap-5" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))' }}>
              <Panel title="Rejected: an approximate vector index (HNSW)" bodyClass="p-4 space-y-3" subtitle={`Distance-based recall@10 against brute force, ${fmtInt(st.vector_ann.embedded_notes)} notes, ${fmtInt(st.vector_ann.distinct_vectors)} distinct vectors. scripts/index_studies.py`}>
                <table className="t num" data-testid="hnsw-table"><thead><tr><th>index over</th><th className="r">ef_search</th><th className="r">canary-text queries</th><th className="r">stored-vector queries</th><th className="r">ms</th></tr></thead><tbody>
                  {Object.entries<any>(st.vector_ann.variants).flatMap(([variant, r]) => Object.entries<any>(r.ef_search).map(([ef, x], i) => (
                    <tr key={variant + ef}><td>{i === 0 ? (variant === 'all_rows' ? 'all rows' : 'distinct vectors') : ''}</td><td className="r">{ef}</td>
                      <td className={cx('r', x.realistic_queries.distance_recall_at_10 < 0.95 && 'text-leak-ink')}>{x.realistic_queries.distance_recall_at_10.toFixed(3)}</td>
                      <td className={cx('r', x.stored_vector_queries.distance_recall_at_10 < 0.95 && 'text-leak-ink')}>{x.stored_vector_queries.distance_recall_at_10.toFixed(3)}</td>
                      <td className="r text-ink-3">{x.realistic_queries.median_ms}</td></tr>)))}
                </tbody></table>
                <Banner tone="plain">Never above {Math.max(...Object.values<any>(st.vector_ann.variants).flatMap((r: any) => Object.values<any>(r.ef_search).map((x: any) => x.realistic_queries.distance_recall_at_10))).toFixed(2)}: the embedding is a hashed bag of words, and one query sees only {st.vector_ann.distinct_distance_values_one_query} distinct distances across {fmtInt(st.vector_ann.distinct_vectors)} vectors. A compliance search that silently drops close paraphrases is worse than a slower one, so v2 searches exactly, over distinct vectors only (the result equals brute force).</Banner>
              </Panel>
              <Panel title="Rejected: a BRIN index on event time" bodyClass="p-4 space-y-3" subtitle="The log is written in time order, so BRIN looked ideal: 32 kB instead of 12 MB. scripts/index_studies.py">
                <table className="t num" data-testid="brin-table"><thead><tr><th>query</th>{['none', 'brin_32', 'btree'].map((k) => <th key={k} className="r">{k === 'none' ? 'no index' : k === 'brin_32' ? `BRIN (${st.time_index.brin_size})` : `B-tree (${st.time_index.btree_size})`}</th>)}</tr></thead><tbody>
                  {Object.keys(st.time_index.none).map((q) => (
                    <tr key={q}><td>{q}</td>{['none', 'brin_32', 'btree'].map((k) => { const v = st.time_index[k][q]; const best = Math.min(...['none', 'brin_32', 'btree'].map((kk) => st.time_index[kk][q])); return <td key={k} className={cx('r', v === best && 'font-semibold', v > best * 5 && 'text-leak-ink')}>{v} ms</td> })}</tr>))}
                </tbody></table>
                <Banner tone="plain">BRIN wins the wide campaign window, but the planner also picks it for the per-slot two-second windows, where each lookup must read whole 32-page block ranges. A plain B-tree is as fast or faster on every query that exists.</Banner>
              </Panel>
            </div>)}
          <div className="text-[0.8rem] text-ink-3 flex gap-2 flex-wrap">{Object.keys(bm).sort().map((k) => <Chip key={k}>{k}.json{bm[k].when ? ` · ${bm[k].when}` : ''}</Chip>)}</div>
        </div>)}
    </div>
  )
}
