import { useState } from 'react'
import { Banner, Chip, EmptyState, IconCheck, IconX, PageHeader, Panel, RoleChip, SqlBlock } from '../components/ui'
import { api, useApi } from '../lib/api'
import { cx } from '../lib/format'

interface Step { role: string; sql: string; caption: string; setup: boolean; ok?: boolean; rows?: any[]; rowcount?: number; error?: { message: string; sqlstate: string; detail?: string; hint?: string; context?: string; constraint?: string; full: string } }
interface CaseDef { id: string; title: string; doc: string; blurb: string; steps: Step[] }
interface Result { id: string; title: string; doc: string; blurb: string; rolled_back: boolean; elapsed_ms: number; steps: Step[] }

function StepView({ s, n }: { s: Step; n: number }) {
  return (
    <li className={cx('rounded-lg border p-3 space-y-2', s.setup ? 'border-line-2 bg-surface-2' : 'border-line bg-surface')} data-step={n} data-ok={s.ok ? '1' : '0'} data-setup={s.setup ? '1' : '0'}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="num text-ink-3 text-[0.8rem] w-4">{n}</span><RoleChip role={s.role} />{s.setup && <Chip>setup</Chip>}
        <span className="text-[0.9rem] text-ink-2">{s.caption}</span>
      </div>
      <SqlBlock sql={s.sql} />
      {s.ok === false && s.error && (
        <div className="rounded-lg border border-line bg-surface-3 p-3" data-testid="pg-error">
          <div className="flex items-center gap-2 text-[0.8rem] font-semibold tracking-wide"><IconX size={15} />REFUSED BY POSTGRESQL<span className="mono font-medium text-ink-3">SQLSTATE {s.error.sqlstate}</span></div>
          <div className="mono text-[0.86rem] mt-1.5 break-words" data-testid="pg-error-text">{s.error.message}</div>
          {s.error.detail && <div className="mono text-[0.78rem] text-ink-3 mt-1 break-words">DETAIL: {s.error.detail}</div>}
          {s.error.hint && <div className="mono text-[0.78rem] text-ink-3 break-words">HINT: {s.error.hint}</div>}
          {s.error.context && <div className="mono text-[0.74rem] text-ink-3 mt-1 whitespace-pre-wrap">CONTEXT: {s.error.context}</div>}
        </div>)}
      {s.ok === true && (
        <div className="rounded-lg border border-steel-line bg-steel-bg p-3">
          <div className="flex items-center gap-2 text-[0.8rem] font-semibold text-steel"><IconCheck size={15} />{s.rowcount ? `${s.rowcount} row${s.rowcount > 1 ? 's' : ''}` : 'OK'}{s.rows?.length ? '' : ''}</div>
          {s.rows && s.rows.length > 0 && <pre className="mono text-[0.8rem] mt-1.5 overflow-x-auto">{s.rows.map((r) => JSON.stringify(r)).join('\n')}</pre>}
          {s.rows && s.rows.length === 0 && !s.setup && <div className="text-[0.8rem] text-ink-3">statement succeeded</div>}
          {s.rows && s.rows.length > 0 && Object.values(s.rows[0]).every((v) => v === 0) && <div className="text-[0.8rem] text-ink-2 mt-1">0 rows visible — not an error: row-level security filters silently.</div>}
        </div>)}
    </li>
  )
}

export default function RulesLab() {
  const cases = useApi<{ cases: CaseDef[]; enabled: boolean }>('/api/lab/cases')
  const [res, setRes] = useState<Result | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  async function run(id: string) {
    setBusy(id); setErr(null)
    try { setRes(await api<Result>(`/api/lab/${id}`, { method: 'POST' })) } catch (e: any) { setErr(e.message) } finally { setBusy(null) }
  }
  const list = cases.data?.cases ?? []
  return (
    <div>
      <PageHeader title="DB Rules Lab" lead="Fixed buttons only; there is no free-form SQL box. Each button tries a forbidden operation under the real database role, inside a transaction that is always rolled back, and shows PostgreSQL's own error text." />
      <div className="mb-3"><Banner tone="plain">Everything runs as a real role (<span className="mono">SET ROLE</span>). Where a trigger is the point, the privilege layer refuses first and the lab then steps up to the table owner to show the trigger firing even for the owner. The lab login is a separate non-superuser account used only here.</Banner></div>
      <div className="grid gap-5" style={{ gridTemplateColumns: 'minmax(0, 5fr) minmax(0, 7fr)' }}>
        <Panel title="Forbidden operations" subtitle={`${list.length} fixed scenarios`}
          sql={list.flatMap((c) => c.steps.map((st, i) => ({ name: `${c.id} · step ${i + 1}${st.setup ? ' (setup)' : ''}`, role: st.role, sql: st.sql, note: st.caption })))} loading={cases.loading && !cases.data} error={cases.error} onRetry={cases.reload} bodyClass="p-3">
          <ul className="space-y-2.5" data-testid="lab-cases">
            {list.map((c) => (
              <li key={c.id} className={cx('rounded-lg border p-3 flex items-start gap-3', res?.id === c.id ? 'border-steel bg-steel-bg' : 'border-line bg-surface')}>
                <div className="min-w-0 flex-1">
                  <div className="font-semibold leading-5">{c.title}</div>
                  <div className="text-[0.76rem] text-ink-3 mb-1">proposal {c.doc}</div>
                  <div className="text-[0.86rem] text-ink-2 leading-5">{c.blurb}</div>
                </div>
                <button className="btn btn-sm shrink-0" disabled={!!busy || !cases.data?.enabled} onClick={() => run(c.id)} data-testid={`lab-run-${c.id}`}>{busy === c.id ? 'Running…' : 'Try it'}</button>
              </li>))}
          </ul>
        </Panel>
        <Panel title={res ? res.title : 'Result'} subtitle={res ? <>proposal {res.doc} · {res.elapsed_ms} ms</> : 'Choose a scenario'} bodyClass="p-3" sql={res ? res.steps.map((s, i) => ({ name: `step ${i + 1}${s.setup ? ' (setup)' : ''}`, role: s.role, sql: s.sql, note: s.caption })) : undefined}
          actions={res ? <Chip tone="steel" className="gap-1"><IconCheck size={12} />rolled back</Chip> : null}>
          {err && <Banner tone="leak">{err}</Banner>}
          {!res && !err ? <EmptyState title="Nothing run yet" icon={<IconX size={26} />}>Pick a scenario on the left. Every statement and every error shown here is real; nothing is committed.</EmptyState> : res && (
            <div className="space-y-3" data-testid="lab-result"><p className="text-[0.9rem] text-ink-2 px-1">{res.blurb}</p>
              <ol className="space-y-2.5">{res.steps.map((s, i) => <StepView key={i} s={s} n={i + 1} />)}</ol></div>)}
        </Panel>
      </div>
    </div>
  )
}
