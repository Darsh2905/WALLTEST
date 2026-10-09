/** Live-run state. One EventSource to /api/stream for the whole app; events are batched every 100 ms so a 1,500-slot simulated
 *  run does not re-render the page 1,500 times. Everything here was read from the database by the engine. */
import { createContext, useContext, useEffect, useMemo, useReducer, useRef } from 'react'
import { api } from './api'

export type Gate = 'vector' | 'notes' | 'cache'
const ASSET_GATE: Record<string, Gate> = { vector_memory: 'vector', notes_table: 'notes', feature_cache: 'cache' }

export interface SlotView {
  index: number; slot_id: number; commitment: string; committed_at: string; start: string; end: string; treatment_id: number
  channels: { vector_memory: boolean; notes_table: boolean; cache: boolean }
  state: 'committed' | 'open' | 'revealed'; flip_bit?: number; commitment_ok?: boolean
}
export interface Pulse { id: number; gate: Gate; kind: 'write' | 'read'; agent: string; denied: boolean; at: number }
export interface AccessEvt { event_id: number; agent_name: string; asset_name: string; op: string; outcome: string; detail: string | null; row_ref: string | null; event_time: string }

export interface RunState {
  connected: boolean
  status: { state: string; campaign_id: number | null; running: boolean; error: string | null; slots_done: number; planned: number } | null
  campaign: any | null
  slots: Record<number, SlotView>
  order: number[]
  agents: Record<string, { n: number; correct: number }>
  series: Record<string, number[]>
  slotsDone: number
  pulses: Pulse[]
  recent: AccessEvt[]
  gateTotals: Record<Gate, { allowed: number; denied: number }>
  openSlot: { index: number; channels: SlotView['channels'] } | null
  /** SEQUENTIAL inference only: ln E per agent after every slot (anytime-valid, so watching it is legitimate) */
  evalues: Record<string, { x: number; y: number }[]>
  evThreshold: number | null
  checks: { slots_done: number; stop: boolean; rule: string; agents: any[] }[]
  snapshot: { sha256: string; signature: string; pubkey: string; evidence_root: string } | null
  stoppedEarly: boolean
  frozen: boolean
  finished: boolean
  error: string | null
  cancelled: boolean
}

const empty = (): RunState => ({
  connected: false, status: null, campaign: null, slots: {}, order: [], agents: {}, series: {}, slotsDone: 0, pulses: [], recent: [],
  gateTotals: { vector: { allowed: 0, denied: 0 }, notes: { allowed: 0, denied: 0 }, cache: { allowed: 0, denied: 0 } },
  openSlot: null, evalues: {}, evThreshold: null, checks: [], snapshot: null, stoppedEarly: false,
  frozen: false, finished: false, error: null, cancelled: false,
})

let pulseId = 0
function reduce(s: RunState, a: any): RunState {
  switch (a.t) {
    case 'conn': return { ...s, connected: a.v }
    case 'hello': return { ...s, status: a.status }
    case 'reset': return { ...empty(), connected: s.connected, status: s.status }
    case 'batch': {
      let st = { ...s, slots: { ...s.slots }, agents: { ...s.agents }, series: { ...s.series }, gateTotals: { ...s.gateTotals }, pulses: [...s.pulses], recent: [...s.recent],
                 evalues: { ...s.evalues }, checks: [...s.checks] }
      const now = performance.now()
      for (const e of a.events) {
        switch (e.type) {
          case 'campaign_created':
            st = { ...empty(), connected: st.connected, status: st.status, campaign: e }
            st.slots = {}; st.series = {}
            for (const ag of e.agents) st.series[ag] = []
            for (const ag of e.agents) st.agents[ag] = { n: 0, correct: 0 }
            break
          case 'slot_committed':
            st.slots[e.index] = { index: e.index, slot_id: e.slot_id, commitment: e.commitment, committed_at: e.committed_at, start: e.start, end: e.end,
                                  treatment_id: e.treatment_id, channels: e.channels, state: 'committed' }
            st.order = [...st.order, e.index]
            break
          case 'slot_open':
            if (st.slots[e.index]) { st.slots[e.index] = { ...st.slots[e.index], state: 'open' }; st.openSlot = { index: e.index, channels: st.slots[e.index].channels } }
            break
          case 'slot_revealed':
            if (st.slots[e.index]) st.slots[e.index] = { ...st.slots[e.index], state: 'revealed', flip_bit: e.flip_bit, commitment_ok: e.commitment_ok }
            break
          case 'progress':
            st.slotsDone = e.slots_done
            for (const [k, v] of Object.entries<any>(e.agents)) {
              st.agents[k] = v
              const arr = st.series[k] ? [...st.series[k]] : []
              arr[v.n - 1] = v.correct
              st.series[k] = arr
            }
            break
          case 'access_events':
            for (const ev of e.events as AccessEvt[]) {
              const gate = ASSET_GATE[ev.asset_name]
              st.recent.unshift(ev)
              if (gate) {
                const denied = ev.outcome === 'DENIED'
                st.gateTotals[gate] = { allowed: st.gateTotals[gate].allowed + (denied ? 0 : 1), denied: st.gateTotals[gate].denied + (denied ? 1 : 0) }
                st.pulses.push({ id: ++pulseId, gate, kind: ev.op === 'READ' ? 'read' : 'write', agent: ev.agent_name, denied, at: now })
              }
            }
            st.recent = st.recent.slice(0, 40)
            break
          case 'evalues':
            st.evThreshold = e.threshold_log_e
            for (const [k, v] of Object.entries<any>(e.agents)) {
              const arr = st.evalues[k] ? [...st.evalues[k]] : []
              arr.push({ x: v.n, y: v.log_e })
              st.evalues[k] = arr
            }
            break
          case 'sequential_check': st.checks.push(e); break
          case 'verdict_frozen': st.frozen = true; st.snapshot = e.snapshot ?? null; st.stoppedEarly = !!e.stopped_early; break
          case 'run_finished': st.finished = true; st.openSlot = null; break
          case 'run_cancelled': st.cancelled = true; st.finished = true; break
          case 'run_error': st.error = e.message; st.finished = true; break
        }
      }
      st.pulses = st.pulses.filter((p) => now - p.at < 1500).slice(-60)
      return st
    }
    case 'prune': return { ...s, pulses: s.pulses.filter((p) => performance.now() - p.at < 1500) }
    default: return s
  }
}

interface Ctx { state: RunState; start: (body: any) => Promise<{ campaign_id: number }>; cancel: () => Promise<void>; refreshStatus: () => void }
const RunContext = createContext<Ctx | null>(null)
export const useRun = () => { const c = useContext(RunContext); if (!c) throw new Error('RunProvider missing'); return c }

const EVENT_TYPES = ['campaign_created', 'campaign_started', 'slot_committed', 'slot_open', 'access_events', 'orders', 'slot_revealed',
                     'progress', 'evalues', 'sequential_check', 'verdict_frozen', 'run_finished', 'run_cancelled', 'run_error']

export function RunProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reduce, undefined, empty)
  const buf = useRef<any[]>([])
  useEffect(() => {
    const es = new EventSource('/api/stream')
    es.onopen = () => dispatch({ t: 'conn', v: true })
    es.onerror = () => dispatch({ t: 'conn', v: false })
    es.addEventListener('hello', (m: MessageEvent) => dispatch({ t: 'hello', status: JSON.parse(m.data) }))
    for (const t of EVENT_TYPES) es.addEventListener(t, (m: MessageEvent) => buf.current.push(JSON.parse(m.data)))
    const timer = window.setInterval(() => {
      if (buf.current.length) { const events = buf.current; buf.current = []; dispatch({ t: 'batch', events }) }
      else dispatch({ t: 'prune' })
    }, 100)
    return () => { es.close(); window.clearInterval(timer) }
  }, [])
  const value = useMemo<Ctx>(() => ({
    state,
    start: async (body) => { dispatch({ t: 'reset' }); buf.current = []; return api('/api/runs', { method: 'POST', body: JSON.stringify(body) }) },
    cancel: async () => { await api('/api/runs/current/cancel', { method: 'POST' }) },
    refreshStatus: () => { api<any>('/api/runs/current').then((s) => dispatch({ t: 'hello', status: s })).catch(() => {}) },
  }), [state])
  return <RunContext.Provider value={value}>{children}</RunContext.Provider>
}
