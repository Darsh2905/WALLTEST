import { useEffect, useState } from 'react'
import { Banner, Chip, EmptyState, IconCheck, IconShield, IconWarn, IconX, Panel } from './ui'
import { Leaf, ProofStep, SqlMeta, Snapshot, api, useApi } from '../lib/api'
import { ed25519Verify, leafHash, merkleRoot, rootFromProof, sha256TextHex, SigResult } from '../lib/evidence'
import { cx, fmtInt, shortHash } from '../lib/format'

type Check = 'pending' | 'running' | 'ok' | 'fail' | 'unsupported'
const Mark = ({ c }: { c: Check }) => c === 'ok' ? <IconCheck size={16} className="text-steel" /> : c === 'fail' ? <IconX size={16} className="text-leak-ink" />
  : c === 'unsupported' ? <IconWarn size={15} className="text-ink-3" /> : c === 'running' ? <span className="live-dot" style={{ width: 8, height: 8 }} /> : <span className="w-4 h-4 rounded-full border border-line inline-block" />

/** Verify a frozen campaign in the browser, trusting nothing the server says about itself:
 *  SHA-256 of the snapshot text, the Ed25519 signature, the Merkle root rebuilt from every evidence row, and inclusion proofs. */
export function EvidencePanel({ cid }: { cid: number }) {
  const snap = useApi<Snapshot>(`/api/campaigns/${cid}/snapshot`, [cid])
  const [leaves, setLeaves] = useState<Leaf[] | null>(null)
  const [leafSql, setLeafSql] = useState<SqlMeta[]>([])
  const [checks, setChecks] = useState<Record<string, Check>>({ sha: 'pending', sig: 'pending', root: 'pending' })
  const [rootSeen, setRootSeen] = useState<string | null>(null)
  const [levels, setLevels] = useState<number[]>([])
  const [tampered, setTampered] = useState(false)
  const [sel, setSel] = useState<number>(0)
  const [proof, setProof] = useState<{ leaf: Leaf & { payload: string }; proof: ProofStep[]; leafOk: boolean; rootOk: boolean } | null>(null)
  useEffect(() => { setChecks({ sha: 'pending', sig: 'pending', root: 'pending' }); setLeaves(null); setProof(null); setRootSeen(null); setTampered(false) }, [cid])

  async function verify(tamper = false) {
    const s = snap.data
    if (!s) return
    setTampered(tamper)
    const message = tamper ? s.message.replace(/\|(LEAK|NO_EVIDENCE)\|/, (m) => (m === '|LEAK|' ? '|NO_EVIDENCE|' : '|LEAK|')) : s.message
    setChecks({ sha: 'running', sig: 'running', root: 'running' })
    const sha = await sha256TextHex(message)
    setChecks((c) => ({ ...c, sha: sha === s.snapshot_sha256.trim() ? 'ok' : 'fail' }))
    const sig: SigResult = await ed25519Verify(s.signer_pubkey, message, s.signature)
    setChecks((c) => ({ ...c, sig: sig === 'valid' ? 'ok' : sig === 'invalid' ? 'fail' : 'unsupported' }))
    let ls = leaves
    if (!ls) {
      const r = await api<{ data: { leaves: Leaf[] }; sql: SqlMeta[] }>(`/api/campaigns/${cid}/evidence`)
      ls = r.data.leaves; setLeaves(ls); setLeafSql(r.sql)
    }
    const lv: number[] = [ls.length]
    const root = await merkleRoot(ls.map((l) => l.leaf_hash), (n) => lv.push(n))
    setLevels(lv); setRootSeen(root)
    setChecks((c) => ({ ...c, root: root === s.evidence_root.trim() ? 'ok' : 'fail' }))
  }

  async function prove(idx: number) {
    const r = await api<{ data: { leaf: Leaf & { payload: string }; proof: ProofStep[] } }>(`/api/campaigns/${cid}/evidence/${idx}`)
    const h = await leafHash(r.data.leaf.payload)
    const root = await rootFromProof(h, r.data.proof)
    setProof({ ...r.data, leafOk: h === r.data.leaf.leaf_hash, rootOk: root === snap.data?.evidence_root.trim() })
  }

  if (snap.error) return (
    <Panel title="Signed evidence"><EmptyState title="No signed snapshot" icon={<IconShield size={26} />}>{snap.error.includes('v1') || snap.error.includes('not frozen') ? 'This campaign is not frozen yet, or was frozen by v1 (which kept only per-row hashes).' : snap.error}</EmptyState></Panel>)
  const s = snap.data
  const rows: [string, string, Check, React.ReactNode][] = s ? [
    ['sha', 'snapshot text hashes to the signed SHA-256', checks.sha, <span className="mono">{shortHash(s.snapshot_sha256, 16)}…</span>],
    ['sig', 'Ed25519 signature by the engine key verifies', checks.sig, <span className="mono">key {shortHash(s.signer_pubkey, 16)}…{s.signer_pubkey === s.engine_pubkey ? ' (this engine)' : ' (another key!)'}</span>],
    ['root', `Merkle root rebuilt in your browser from ${fmtInt(s.evidence_leaves)} evidence rows`, checks.root, <span className="mono">{rootSeen ? shortHash(rootSeen, 16) + '…' : '—'} vs {shortHash(s.evidence_root, 16)}…</span>],
  ] : []
  const kinds = leaves ? Object.entries(leaves.reduce<Record<string, number>>((o, l) => ((o[l.kind] = (o[l.kind] ?? 0) + 1), o), {})) : []

  return (
    <Panel title="Signed evidence: verify the frozen verdict in your browser" sql={[...snap.sql, ...leafSql]} loading={snap.loading && !s} bodyClass="p-4 space-y-4"
      subtitle="The engine signed one canonical text (evidence Merkle root + every hypothesis' result) with a key the database never sees. Edit a row, a verdict or the log and one of these checks fails."
      actions={s ? <div className="flex gap-2"><button className="btn btn-sm btn-primary" onClick={() => verify(false)} data-testid="verify-evidence">Verify in browser</button>
        <button className="btn btn-sm" onClick={() => verify(true)} title="Flip one verdict in the text before checking" data-testid="tamper-evidence">Tamper with a verdict</button></div> : null}>
      {s && (<>
        <ul className="space-y-2" data-testid="evidence-checks">
          {rows.map(([k, label, c, detail]) => (
            <li key={k} className={cx('rounded-lg border px-3 py-2 flex items-center gap-3', c === 'ok' ? 'border-steel-line bg-steel-bg' : c === 'fail' ? 'border-leak-line bg-leak-bg' : 'border-line')} data-check={k} data-state={c}>
              <Mark c={c} /><span className="font-medium flex-1">{label}</span><span className="text-[0.8rem] text-ink-2">{c === 'unsupported' ? 'this browser has no Ed25519 in Web Crypto' : detail}</span>
            </li>))}
        </ul>
        {tampered && checks.sha !== 'running' && <Banner tone="leak">One verdict in the text was flipped before checking: the hash and the signature no longer match. The evidence root still matches, since the evidence rows themselves were untouched.</Banner>}
        <div className="text-[0.8rem] text-ink-3">Server's own checks (not used above): {Object.entries(s.server_checks).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v ? '✓' : '✗'}`).join(' · ')}</div>
        {levels.length > 0 && <div className="text-[0.8rem] text-ink-3 num">tree levels: {levels.join(' → ')}</div>}
        <details className="text-[0.84rem]"><summary className="cursor-pointer text-ink-2">The signed text ({s.message.split('\n').length - 1} hypotheses)</summary>
          <pre className="mono text-[0.74rem] mt-2 max-h-56 overflow-auto bg-surface-2 border border-line rounded-lg p-2.5 whitespace-pre">{s.message}</pre></details>
        {leaves && (
          <div className="border-t border-line-2 pt-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2"><span className="eyebrow">inclusion proof for one row</span>
              {kinds.map(([k, n]) => <Chip key={k}>{k} {fmtInt(n)}</Chip>)}</div>
            <div className="flex items-center gap-2">
              <select className="btn btn-sm max-w-[22rem]" value={sel} onChange={(e) => setSel(Number(e.target.value))} aria-label="Evidence row">
                {leaves.filter((_, i) => i < 400 || i % Math.ceil(leaves.length / 400) === 0).map((l) => <option key={l.ord} value={l.ord}>#{l.ord} · {l.ref}</option>)}
              </select>
              <button className="btn btn-sm" onClick={() => prove(sel)} data-testid="prove-leaf">Prove inclusion</button>
            </div>
            {proof && (
              <div className={cx('rounded-lg border p-3 space-y-1.5', proof.leafOk && proof.rootOk ? 'border-steel-line bg-steel-bg' : 'border-leak-line bg-leak-bg')} data-testid="proof-result" data-ok={proof.leafOk && proof.rootOk ? '1' : '0'}>
                <div className="font-semibold">{proof.leafOk && proof.rootOk ? `Row ${proof.leaf.ref} is in the signed evidence` : 'Proof FAILED'}</div>
                <div className="mono text-[0.74rem] break-all text-ink-2 max-h-24 overflow-auto">{proof.leaf.payload}</div>
                <div className="text-[0.78rem] text-ink-3 num">leaf hash recomputed {proof.leafOk ? '✓' : '✗'} · {proof.proof.length} levels to the root {proof.rootOk ? '✓' : '✗'} · a proof needs only {proof.proof.filter((p) => p.side !== 'P').length} sibling hashes, not the other {fmtInt(leaves.length - 1)} rows</div>
              </div>)}
          </div>)}
      </>)}
    </Panel>
  )
}
