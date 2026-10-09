import { Suspense, lazy, useEffect, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { useApi } from './lib/api'
import { useRun } from './lib/run'
import { Chip, IconMoon, IconSun } from './components/ui'
import { cx } from './lib/format'
import Wall from './pages/Wall'

// Code splitting: the first paint needs only the Wall; every other page (and d3-heavy charts) loads on navigation.
const RunAudit = lazy(() => import('./pages/RunAudit'))
const Verdicts = lazy(() => import('./pages/Verdicts'))
const Inspector = lazy(() => import('./pages/Inspector'))
const RulesLab = lazy(() => import('./pages/RulesLab'))
const Compliance = lazy(() => import('./pages/Compliance'))
const Power = lazy(() => import('./pages/Power'))
const Performance = lazy(() => import('./pages/Performance'))
const Schema = lazy(() => import('./pages/Schema'))

const NAV = [
  { to: '/', label: 'Wall', hint: 'live barrier view', end: true },
  { to: '/run', label: 'Run audit', hint: 'configure & start' },
  { to: '/verdicts', label: 'Verdicts', hint: 'statistics & attribution' },
  { to: '/inspector', label: 'Commit–reveal', hint: 'verify in the browser' },
  { to: '/lab', label: 'DB Rules Lab', hint: 'try forbidden operations' },
  { to: '/compliance', label: 'Compliance', hint: 'SDD · grants · exposure' },
  { to: '/power', label: 'Power', hint: 'sample size · peeking' },
  { to: '/performance', label: 'Performance', hint: 'v1 vs v2, measured' },
  { to: '/schema', label: 'Schema', hint: '20 tables · ER diagram' },
]

function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => (document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'))
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try { localStorage.setItem('walltest-theme', theme) } catch { /* storage unavailable: theme still applies for this session */ }
  }, [theme])
  return [theme, () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))] as const
}

export default function App() {
  const [theme, toggle] = useTheme()
  const { state } = useRun()
  const meta = useApi<any>('/api/meta')
  const prices = meta.data?.prices
  const running = !!state.campaign && !state.finished
  const sim = state.campaign?.simulated_clock

  return (
    <div className="min-h-screen flex">
      <aside className="w-[212px] shrink-0 border-r border-line bg-surface flex flex-col sticky top-0 h-screen">
        <div className="px-4 pt-5 pb-4 border-b border-line-2">
          <div className="flex items-center gap-2.5">
            <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="7" fill="var(--ink)" /><path d="M16 6v20M7 11h18M7 21h18" stroke="var(--bg)" strokeWidth="2.2" fill="none" /><circle cx="16" cy="16" r="3.2" fill="var(--leak)" /></svg>
            <div><div className="font-semibold tracking-tight leading-5 text-[1.12rem]">WALLTEST</div><div className="text-[0.7rem] text-ink-3 leading-3 tracking-wide">information-barrier audit</div></div>
          </div>
        </div>
        <nav className="p-2 flex-1 overflow-y-auto" aria-label="Pages">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => cx('block rounded-md px-3 py-2 mb-0.5 border border-transparent', isActive ? 'bg-surface-3 border-line' : 'hover:bg-surface-2')}>
              {({ isActive }) => (<><div className={cx('text-[0.95rem] leading-5', isActive ? 'font-semibold' : 'font-medium text-ink-2')}>{n.label}</div><div className="text-[0.72rem] text-ink-3 leading-4">{n.hint}</div></>)}
            </NavLink>
          ))}
        </nav>
        <div className="p-3 border-t border-line-2 text-[0.72rem] text-ink-3 leading-4">
          Every number on screen is read from PostgreSQL 16 through a non-owner role.
        </div>
      </aside>

      <div className="flex-1 min-w-0 flex flex-col">
        <header className="sticky top-0 z-30 bg-bg/90 backdrop-blur border-b border-line px-6 py-2.5 flex items-center gap-2 flex-wrap">
          {meta.loading ? <span className="skeleton" style={{ height: 22, width: 260 }} /> : meta.error ? <Chip tone="leak">API unreachable</Chip> : prices?.synthetic
            ? <Chip tone="leak" title="Prices are generated, not market data">SYNTHETIC PRICE DATA</Chip>
            : <Chip tone="steel" title={prices?.provenance}>REAL NIFTY 50 EOD · {prices?.n_securities} stocks · {prices?.first_date} → {prices?.last_date}</Chip>}
          <Chip title="UPSI items and canary texts are invented for this lab">synthetic UPSI &amp; canaries</Chip>
          <Chip title="research / leaky / clean / partial agents are scripted validation instruments with planted ground truth">scripted agents = validation instruments</Chip>
          <div className="flex-1" />
          {running && <Chip tone="steel" className="gap-2"><span className="live-dot" />{sim ? 'SIMULATED CLOCK' : 'LIVE'} · campaign #{state.campaign.campaign_id} · {state.slotsDone}/{state.campaign.planned_slots}</Chip>}
          <span className={cx('text-[0.78rem] flex items-center gap-1.5', state.connected ? 'text-ink-3' : 'text-leak-ink')} title="Server-sent events connection">
            <span className={cx('inline-block w-2 h-2 rounded-full', state.connected ? 'bg-steel' : 'bg-leak')} />{state.connected ? 'stream connected' : 'stream offline'}
          </span>
          <button className="btn btn-sm" onClick={toggle} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>{theme === 'dark' ? <IconSun size={14} /> : <IconMoon size={14} />}{theme === 'dark' ? 'Light' : 'Dark'}</button>
        </header>
        <main className="flex-1 px-6 py-6 min-w-0">
          <Suspense fallback={<div className="card"><div className="skeleton m-4" style={{ height: 240 }} /></div>}>
          <Routes>
            <Route path="/" element={<Wall />} />
            <Route path="/run" element={<RunAudit />} />
            <Route path="/verdicts" element={<Verdicts />} />
            <Route path="/verdicts/:cid" element={<Verdicts />} />
            <Route path="/inspector" element={<Inspector />} />
            <Route path="/inspector/:cid" element={<Inspector />} />
            <Route path="/lab" element={<RulesLab />} />
            <Route path="/compliance" element={<Compliance />} />
            <Route path="/power" element={<Power />} />
            <Route path="/performance" element={<Performance />} />
            <Route path="/schema" element={<Schema />} />
            <Route path="*" element={<div className="card p-8 text-center">Page not found.</div>} />
          </Routes>
          </Suspense>
        </main>
      </div>
    </div>
  )
}
