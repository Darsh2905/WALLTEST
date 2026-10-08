import { useEffect, useRef, useState } from 'react'
import { SqlMeta } from '../lib/api'
import { cx } from '../lib/format'

/* ---------- icons (inline SVG, 16px) -------------------------------------------------------------------------------------- */
type IconProps = { size?: number; className?: string }
const I = ({ d, size = 16, className, fill }: IconProps & { d: string; fill?: boolean }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={fill ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
    <path d={d} />
  </svg>
)
export const IconLock = (p: IconProps) => <I {...p} d="M7 11V8a5 5 0 0 1 10 0v3M5 11h14v10H5z" />
export const IconCheck = (p: IconProps) => <I {...p} d="M4 12.5l5 5L20 6.5" />
export const IconX = (p: IconProps) => <I {...p} d="M6 6l12 12M18 6L6 18" />
export const IconCode = (p: IconProps) => <I {...p} d="M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16" />
export const IconCopy = (p: IconProps) => <I {...p} d="M9 9h11v11H9zM5 15V4h11" />
export const IconPlay = (p: IconProps) => <I {...p} fill d="M7 4.5v15l13-7.5z" />
export const IconStop = (p: IconProps) => <I {...p} fill d="M6 6h12v12H6z" />
export const IconSun = (p: IconProps) => <I {...p} d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z" />
export const IconMoon = (p: IconProps) => <I {...p} d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z" />
export const IconWarn = (p: IconProps) => <I {...p} d="M12 4l9.5 16h-19zM12 10v4.5M12 17.5v.1" />
export const IconShield = (p: IconProps) => <I {...p} d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />

/* ---------- chips ---------------------------------------------------------------------------------------------------------- */
export function VerdictChip({ verdict, size = 'md' }: { verdict: string | null | undefined; size?: 'sm' | 'md' | 'lg' }) {
  const pad = size === 'lg' ? 'px-3 py-1.5 text-[0.95rem]' : size === 'sm' ? 'px-1.5 py-0.5 text-[0.72rem]' : 'px-2 py-0.5 text-[0.8rem]'
  if (verdict === 'LEAK')
    return <span className={cx('inline-flex items-center gap-1.5 rounded font-semibold tracking-wide border bg-leak-bg text-leak-ink border-leak-line', pad)}><IconWarn size={size === 'sm' ? 11 : 14} />LEAK</span>
  if (verdict === 'NO_EVIDENCE')
    return <span className={cx('inline-flex items-center gap-1.5 rounded font-semibold tracking-wide border bg-surface-3 text-ink-2 border-line', pad)}>NO EVIDENCE</span>
  return <span className={cx('inline-flex items-center gap-1.5 rounded font-medium tracking-wide border border-dashed text-ink-3 border-line', pad)}><IconLock size={size === 'sm' ? 11 : 13} />WITHHELD</span>
}

export function Chip({ children, tone = 'plain', title, className }: { children: React.ReactNode; tone?: 'plain' | 'steel' | 'leak' | 'solid'; title?: string; className?: string }) {
  const tones = {
    plain: 'bg-surface-3 text-ink-2 border-line', steel: 'bg-steel-bg text-steel border-steel-line',
    leak: 'bg-leak-bg text-leak-ink border-leak-line', solid: 'bg-ink text-bg border-ink',
  }
  return <span title={title} className={cx('inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[0.75rem] font-medium leading-none whitespace-nowrap', tones[tone], className)}>{children}</span>
}
export const RoleChip = ({ role }: { role: string }) => <Chip tone={role === 'walltest_owner' ? 'solid' : 'steel'} className="mono">SET ROLE {role}</Chip>

export function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: 'leak' }) {
  return (
    <div className="min-w-0">
      <div className="eyebrow">{label}</div>
      <div className={cx('num text-[1.55rem] leading-8 font-semibold', tone === 'leak' && 'text-leak')}>{value}</div>
      {sub && <div className="text-[0.82rem] text-ink-3 leading-4">{sub}</div>}
    </div>
  )
}

/* ---------- states --------------------------------------------------------------------------------------------------------- */
export function Skeleton({ h = 16, w = '100%', className }: { h?: number; w?: number | string; className?: string }) {
  return <div className={cx('skeleton', className)} style={{ height: h, width: w }} aria-hidden />
}
export function LoadingBlock({ rows = 4, h = 160 }: { rows?: number; h?: number }) {
  return (
    <div style={{ minHeight: h }} className="space-y-3 p-4" role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} h={14} w={`${92 - i * 11}%`} />)}
    </div>
  )
}
export function EmptyState({ title, children, action, icon }: { title: string; children?: React.ReactNode; action?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center text-center px-6 py-10 gap-2" style={{ minHeight: 160 }}>
      <div className="text-ink-3">{icon ?? <IconShield size={28} />}</div>
      <div className="font-semibold">{title}</div>
      {children && <div className="text-ink-2 max-w-md text-[0.93rem]">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="m-4 rounded-lg border border-leak-line bg-leak-bg p-4 text-leak-ink flex items-start gap-3">
      <IconWarn size={18} className="mt-0.5 shrink-0" />
      <div className="min-w-0">
        <div className="font-semibold">Could not load this panel</div>
        <div className="mono text-[0.82rem] break-words opacity-90">{message}</div>
        {onRetry && <button className="btn btn-sm mt-2" onClick={onRetry}>Retry</button>}
      </div>
    </div>
  )
}

/* ---------- SQL drawer ----------------------------------------------------------------------------------------------------- */
const KW = /\b(SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|ON|AND|OR|NOT|AS|GROUP BY|ORDER BY|LIMIT|OFFSET|WITH|OVER|PARTITION BY|CASE|WHEN|THEN|ELSE|END|IN|IS|NULL|DISTINCT|UNION ALL|LATERAL|FILTER|UNNEST|ORDINALITY|INSERT INTO|VALUES|UPDATE|DELETE|SET|GRANT|TRUNCATE|CASCADE|TO|BY|ASC|DESC|BETWEEN|EXISTS|LIKE|USING|NULLS|ARRAY)\b/g
export function highlightSql(sql: string): React.ReactNode[] {
  // tokenise comments, strings, keywords, numbers; everything stays selectable text
  const out: React.ReactNode[] = []
  const re = /(--[^\n]*)|('(?:[^']|'')*')|(%\(\w+\)s|%s)|(\b\d+(?:\.\d+)?\b)/g
  let last = 0, k = 0
  const pushPlain = (txt: string) => {
    let i = 0
    for (const m of txt.matchAll(KW)) {
      out.push(txt.slice(i, m.index)); out.push(<span key={k++} className="text-steel font-medium">{m[0]}</span>); i = (m.index ?? 0) + m[0].length
    }
    out.push(txt.slice(i))
  }
  for (const m of sql.matchAll(re)) {
    pushPlain(sql.slice(last, m.index))
    const cls = m[1] ? 'text-ink-3 italic' : m[2] ? 'text-leak-ink' : m[3] ? 'text-steel underline decoration-dotted' : 'text-ink-2'
    out.push(<span key={k++} className={cls}>{m[0]}</span>)
    last = (m.index ?? 0) + m[0].length
  }
  pushPlain(sql.slice(last))
  return out
}

export function SqlBlock({ sql }: { sql: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="relative">
      <button className="btn btn-sm absolute right-2 top-2" onClick={() => { navigator.clipboard?.writeText(sql).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200) }).catch(() => {}) }}>
        {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}{copied ? 'Copied' : 'Copy'}
      </button>
      <pre className="mono text-[0.8rem] leading-[1.45] bg-surface-2 border border-line rounded-lg p-3 pr-20 overflow-x-auto whitespace-pre">{highlightSql(sql)}</pre>
    </div>
  )
}

export function SqlDrawer({ open, onClose, title, queries }: { open: boolean; onClose: () => void; title: string; queries: SqlMeta[] }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    ref.current?.focus()
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={`SQL behind ${title}`}>
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div ref={ref} tabIndex={-1} className="absolute right-0 top-0 h-full w-[min(660px,100vw)] bg-surface border-l border-line shadow-2xl flex flex-col outline-none">
        <div className="flex items-center justify-between px-5 py-4 border-b border-line">
          <div>
            <div className="eyebrow">The SQL behind</div>
            <div className="font-semibold text-[1.05rem]">{title}</div>
          </div>
          <button className="btn btn-sm" onClick={onClose} aria-label="Close SQL drawer"><IconX size={14} />Close</button>
        </div>
        <div className="overflow-y-auto p-5 space-y-6">
          {queries.length === 0 && <div className="text-ink-2">No query has run for this panel yet.</div>}
          {queries.map((q, i) => (
            <section key={q.name + i} className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="mono font-medium">{q.name}</span><RoleChip role={q.role} />
              </div>
              {q.note && <p className="text-[0.9rem] text-ink-2">{q.note}</p>}
              <SqlBlock sql={q.sql} />
              {q.params && Object.keys(q.params).length > 0 && (
                <div className="mono text-[0.78rem] text-ink-3">params: {JSON.stringify(q.params)}</div>
              )}
            </section>
          ))}
        </div>
      </div>
    </div>
  )
}

export function SqlButton({ queries, title }: { queries: SqlMeta[]; title: string }) {
  const [open, setOpen] = useState(false)
  return (<>
    <button className="btn btn-sm btn-ghost text-ink-2" onClick={() => setOpen(true)} data-testid="show-sql"><IconCode size={14} />Show SQL</button>
    <SqlDrawer open={open} onClose={() => setOpen(false)} title={title} queries={queries} />
  </>)
}

/* ---------- panel ------------------------------------------------------------------------------------------------------------ */
export function Panel({ title, subtitle, sql, actions, children, className, bodyClass, loading, error, onRetry, id }: {
  title: React.ReactNode; subtitle?: React.ReactNode; sql?: SqlMeta[]; actions?: React.ReactNode; children?: React.ReactNode
  className?: string; bodyClass?: string; loading?: boolean; error?: string | null; onRetry?: () => void; id?: string
}) {
  const [open, setOpen] = useState(false)
  const label = typeof title === 'string' ? title : 'this panel'
  return (
    <section className={cx('card flex flex-col min-w-0', className)} id={id} data-panel={typeof title === 'string' ? title : undefined}>
      <header className="flex items-start justify-between gap-3 px-4 pt-3.5 pb-2.5 border-b border-line-2">
        <div className="min-w-0">
          <h2 className="font-semibold text-[1.02rem] leading-6">{title}</h2>
          {subtitle && <p className="text-[0.86rem] text-ink-3 leading-5">{subtitle}</p>}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {actions}
          {sql && <button className="btn btn-sm btn-ghost text-ink-2" onClick={() => setOpen(true)} data-testid="show-sql"><IconCode size={14} />Show SQL</button>}
        </div>
      </header>
      <div className={cx('min-w-0', bodyClass)}>
        {error ? <ErrorState message={error} onRetry={onRetry} /> : loading ? <LoadingBlock /> : children}
      </div>
      {sql && <SqlDrawer open={open} onClose={() => setOpen(false)} title={label} queries={sql} />}
    </section>
  )
}

export function PageHeader({ title, lead, right }: { title: string; lead?: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="flex items-end justify-between gap-6 mb-5">
      <div className="min-w-0">
        <h1 className="text-[1.6rem] leading-8 font-semibold tracking-tight">{title}</h1>
        {lead && <p className="text-ink-2 mt-1 max-w-3xl">{lead}</p>}
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  )
}

export function Banner({ tone = 'steel', children, icon }: { tone?: 'steel' | 'leak' | 'plain'; children: React.ReactNode; icon?: React.ReactNode }) {
  const t = tone === 'leak' ? 'bg-leak-bg border-leak-line text-leak-ink' : tone === 'steel' ? 'bg-steel-bg border-steel-line text-steel' : 'bg-surface-3 border-line text-ink-2'
  return <div className={cx('rounded-lg border px-3 py-2 text-[0.88rem] flex items-start gap-2', t)}>{icon}<div className="min-w-0">{children}</div></div>
}
