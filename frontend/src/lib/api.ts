import { useCallback, useEffect, useRef, useState } from 'react'

export interface SqlMeta { name: string; role: string; sql: string; note?: string; params?: Record<string, unknown> | null }
export interface Resp<T> { data: T; sql: SqlMeta[] }

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, { ...init, headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) } })
  } catch {
    throw new ApiError('Cannot reach the API. Is the server running?', 0)
  }
  if (!res.ok) {
    let msg = res.statusText
    try { const j = await res.json(); msg = j.detail ?? j.error ?? msg } catch { /* not json */ }
    throw new ApiError(typeof msg === 'string' ? msg : JSON.stringify(msg), res.status)
  }
  return res.json() as Promise<T>
}

export interface ApiState<T> { data: T | null; sql: SqlMeta[]; loading: boolean; error: string | null; reload: () => void }

/** GET helper with loading / error state. `path = null` disables the request. Responses may be Resp<T> or a bare object. */
export function useApi<T>(path: string | null, deps: unknown[] = []): ApiState<T> {
  const [state, setState] = useState<{ data: T | null; sql: SqlMeta[]; loading: boolean; error: string | null }>({ data: null, sql: [], loading: !!path, error: null })
  const [tick, setTick] = useState(0)
  const seq = useRef(0)
  useEffect(() => {
    if (!path) { setState({ data: null, sql: [], loading: false, error: null }); return }
    const my = ++seq.current
    setState((s) => ({ ...s, loading: true, error: null }))
    api<any>(path).then(
      (j) => { if (my === seq.current) setState({ data: ('data' in j && 'sql' in j ? j.data : j) as T, sql: j.sql ?? [], loading: false, error: null }) },
      (e) => { if (my === seq.current) setState({ data: null, sql: [], loading: false, error: e.message }) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, ...deps])
  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { ...state, reload }
}

/** The SQL behind a panel whose data arrives over the live stream. */
export function useSql(names: string[]): SqlMeta[] {
  const r = useApi<{ sql: SqlMeta[] }>(`/api/sql?names=${names.join(',')}`)
  return r.data?.sql ?? []
}

// ---- shared types -------------------------------------------------------------------------------------------------------
export interface Treatment { treatment_id: number; vector_memory_on: boolean; notes_table_on: boolean; cache_on: boolean }
export interface Campaign {
  campaign_id: number; wall_name: string; alpha: number; planned_slots: number; status: string; clock_mode: 'LIVE' | 'SIMULATED'
  started_at: string | null; closed_at: string | null; config: any; n_cells: number; n_low?: number; slots_committed?: number; treatments?: Treatment[]
}
export type Scope = 'AGENT' | 'CELL' | 'CHANNEL'
/** One hypothesis. AGENT = the agent's pooled test (the gate); CELL = one treatment cell; CHANNEL = matched-pair sign test. */
export interface FrozenRow {
  result_id: number; scope: Scope; treatment_id: number | null; channel: string | null; low_agent: string; model_name: string
  vector_memory_on: boolean | null; notes_table_on: boolean | null; cache_on: boolean | null
  n_slots: number; n_correct: number; accuracy: number | null; p_value: number; log10_p: number | null; log_e: number | null; p_adjusted: number
  verdict: 'LEAK' | 'NO_EVIDENCE'; gate_passed: boolean | null; method: string
  leakage_bits: number | null; acc_lower: number; acc_upper: number | null; leakage_bits_lower: number | null; leakage_bits_upper: number | null
  min_detectable_acc: number | null; family_size: number; n_agents: number | null; alpha: number; clock_mode: string; result_hash: string; hash_ok: boolean
}
export interface LiveRow {
  scope: Scope; treatment_id: number | null; channel: string | null; low_agent: string
  vector_memory_on: boolean | null; notes_table_on: boolean | null; cache_on: boolean | null
  n_slots: number; n_correct: number; n_no_trade: number | null; accuracy: number | null; decidable: boolean; planned_cell: number
  inference: 'FIXED' | 'SEQUENTIAL'; method: string; p_raw: number | null; log_e: number | null; p_adj: number | null; gate_passed: boolean | null
  verdict: string | null; acc_lower: number | null; acc_upper: number | null; leakage_bits_lower: number | null; leakage_bits_upper: number | null
  family_size: number; n_agents: number
}
export type AnyRow = FrozenRow | LiveRow
export const pAdj = (r: AnyRow) => ('p_adjusted' in r ? r.p_adjusted : r.p_adj)
export const pRaw = (r: AnyRow) => ('p_value' in r ? r.p_value : r.p_raw)
export interface Effect { low_agent: string; channel: string; acc_on: number | null; acc_off: number | null; n_on: number; n_off: number; main_effect: number | null }
export interface WallVerdict { verdict: 'LEAK' | 'NO_EVIDENCE'; p_adj: number; agents_flagged: number; agents: number }
export interface VerdictsData {
  campaign: Campaign; treatments: Treatment[]; inference: 'FIXED' | 'SEQUENTIAL'; legacy_v1: boolean; is_frozen: boolean
  frozen: FrozenRow[]; live: LiveRow[]; agents: AnyRow[]; cells: AnyRow[]; channels: AnyRow[]; wall: WallVerdict | null
  channel_effect: Effect[]
  access_summary: { asset_name: string; op: string; outcome: string; n: number }[]; no_trade: { low_agent: string; n_no_trade: number; n_slots: number }[]; doc: string
}
export interface Snapshot {
  message: string; snapshot_sha256: string; signature: string; signer_pubkey: string; evidence_root: string; evidence_leaves: number
  n_distinct_snapshots: number; engine_pubkey: string; format: string
  server_checks: { sha256_matches: boolean; signature_valid: boolean; evidence_root_recomputed: boolean; one_snapshot: boolean }
}
export interface Leaf { ord: number; kind: string; ref: string; leaf_hash: string }
export interface ProofStep { level: number; side: 'L' | 'R' | 'P'; sibling: string | null }

export const AGENT_COLOR: Record<string, string> = { 'trader-leaky': 'var(--s-leaky)', 'trader-clean': 'var(--s-clean)', 'trader-partial': 'var(--s-partial)', 'trader-llm': 'var(--ink)' }
export const AGENT_DASH: Record<string, string> = { 'trader-leaky': '', 'trader-clean': '2 4', 'trader-partial': '7 3', 'trader-llm': '10 3 2 3' }
export const AGENT_BLURB: Record<string, string> = {
  'trader-leaky': 'reads vector memory, notes and cache',
  'trader-partial': 'reads ONE channel (vector memory)',
  'trader-clean': 'price momentum only',
  'trader-llm': 'optional LLM agent · not a validation instrument',
}
export const CHANNELS = ['vector_memory', 'notes_table', 'cache'] as const
export const CHANNEL_LABEL: Record<string, string> = { vector_memory: 'Vector memory', notes_table: 'Notes table', cache: 'Cache', feature_cache: 'Cache', notes: 'Notes table', vector: 'Vector memory' }
