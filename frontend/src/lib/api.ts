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
export interface FrozenRow {
  result_id: number; treatment_id: number; low_agent: string; model_name: string
  vector_memory_on: boolean; notes_table_on: boolean; cache_on: boolean
  n_slots: number; n_correct: number; accuracy: number; p_value: number; log10_p: number; p_adjusted: number
  verdict: 'LEAK' | 'NO_EVIDENCE'; leakage_bits: number; acc_lower: number; leakage_bits_lower: number
  min_detectable_acc: number | null; family_size: number; alpha: number; clock_mode: string; result_hash: string; hash_ok: boolean
}
export interface LiveRow {
  low_agent: string; vector_memory_on: boolean; notes_table_on: boolean; cache_on: boolean
  n_slots: number; n_correct: number; n_no_trade: number; accuracy: number; decidable: boolean; planned_cell: number
  p_raw: number | null; p_adj: number | null; verdict: string | null
}
export interface Effect { low_agent: string; channel: string; acc_on: number | null; acc_off: number | null; n_on: number; n_off: number; main_effect: number | null }
export interface VerdictsData {
  campaign: Campaign; treatments: Treatment[]; frozen: FrozenRow[]; live: LiveRow[]; channel_effect: Effect[]
  access_summary: { asset_name: string; op: string; outcome: string; n: number }[]; no_trade: { low_agent: string; n_no_trade: number; n_slots: number }[]; doc: string
}

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
