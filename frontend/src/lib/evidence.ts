/** Browser-side verification of a frozen campaign (db/migrations/013, 014; identical to the SQL and Python):
 *    leaf  = SHA-256(0x00 || UTF-8(kind ':' json))       node = SHA-256(0x01 || left || right)
 *    a level with an odd number of nodes promotes its last node; root of zero leaves = SHA-256('')
 *  and the Ed25519 signature over the snapshot text. Nothing here trusts the server's own "verified" flags. */
import { sha256Fallback } from './commitment'
import type { ProofStep } from './api'

type Bytes = Uint8Array<ArrayBuffer>
export const hexToBytes = (h: string): Bytes => Uint8Array.from((h.trim().match(/../g) ?? []).map((b) => parseInt(b, 16)))
export const bytesToHex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')

async function sha256(bytes: Bytes): Promise<Bytes> {
  const subtle = typeof crypto !== 'undefined' ? crypto.subtle : undefined
  if (subtle) return new Uint8Array(await subtle.digest('SHA-256', bytes))
  return hexToBytes(sha256Fallback(bytes))
}
const cat = (...parts: Bytes[]): Bytes => { const o = new Uint8Array(parts.reduce((n, p) => n + p.length, 0)); let i = 0; for (const p of parts) { o.set(p, i); i += p.length } return o }
const NODE: Bytes = new Uint8Array([1]), LEAF: Bytes = new Uint8Array([0])

export async function leafHash(payload: string): Promise<string> {
  return bytesToHex(await sha256(cat(LEAF, new TextEncoder().encode(payload) as Bytes)))
}

export async function merkleRoot(leafHex: string[], onLevel?: (size: number) => void): Promise<string> {
  if (leafHex.length === 0) return bytesToHex(await sha256(new Uint8Array()))
  let lvl = leafHex.map(hexToBytes)
  while (lvl.length > 1) {
    const next: Promise<Bytes>[] = []
    for (let i = 0; i < lvl.length; i += 2) next.push(i + 1 < lvl.length ? sha256(cat(NODE, lvl[i], lvl[i + 1])) : Promise.resolve(lvl[i]))
    lvl = await Promise.all(next)
    onLevel?.(lvl.length)
  }
  return bytesToHex(lvl[0])
}

/** Walk an inclusion proof from the leaf to the root. */
export async function rootFromProof(leafHex: string, proof: ProofStep[]): Promise<string> {
  let h = hexToBytes(leafHex)
  for (const s of proof) {
    if (s.side === 'L') h = await sha256(cat(NODE, hexToBytes(s.sibling!), h))
    else if (s.side === 'R') h = await sha256(cat(NODE, h, hexToBytes(s.sibling!)))
  }
  return bytesToHex(h)
}

export type SigResult = 'valid' | 'invalid' | 'unsupported'
/** Ed25519 in Web Crypto (Chrome 137+, Firefox 129+, Safari 17+). Older browsers: 'unsupported', never a silent pass. */
export async function ed25519Verify(pubHex: string, message: string, sigHex: string): Promise<SigResult> {
  const subtle = typeof crypto !== 'undefined' ? crypto.subtle : undefined
  if (!subtle) return 'unsupported'
  let key: CryptoKey
  try {
    key = await subtle.importKey('raw', hexToBytes(pubHex), { name: 'Ed25519' }, false, ['verify'])
  } catch {
    return 'unsupported'
  }
  const ok = await subtle.verify({ name: 'Ed25519' }, key, hexToBytes(sigHex), new TextEncoder().encode(message) as Bytes)
  return ok ? 'valid' : 'invalid'
}

export async function sha256TextHex(text: string): Promise<string> {
  return bytesToHex(await sha256(new TextEncoder().encode(text) as Bytes))
}
