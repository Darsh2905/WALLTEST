import { describe, expect, it } from 'vitest'
import { ed25519Verify, leafHash, merkleRoot, rootFromProof } from '../src/lib/evidence'
import vec from './evidence_vectors.json'

/** Vectors generated independently in Python (hashlib + the engine's own Ed25519 signer): the browser code must agree. */
describe('Merkle evidence (RFC 6962 domain separation)', () => {
  it('hashes a leaf with the 0x00 prefix', async () => {
    expect(await leafHash(vec.payload0)).toBe(vec.leaf0)
  })
  it('builds the same root as Python for every size 0..9 (odd levels promote their last node)', async () => {
    for (let n = 0; n <= 9; n++) expect(await merkleRoot(vec.leaves.slice(0, n))).toBe((vec.roots as Record<string, string>)[String(n)])
  })
  it('walks inclusion proofs, including a promoted (P) step', async () => {
    expect(await rootFromProof(vec.leaves[4], vec.proof_7_4 as any)).toBe(vec.roots['7'])
    expect(await rootFromProof(vec.leaves[6], vec.proof_7_6 as any)).toBe(vec.roots['7'])
    expect(await rootFromProof(vec.leaves[5], vec.proof_7_4 as any)).not.toBe(vec.roots['7'])
  })
})

describe('Ed25519 snapshot signature', () => {
  it('accepts the engine signature and rejects any change', async () => {
    const { pub, msg, sig } = vec.ed25519
    expect(await ed25519Verify(pub, msg, sig)).toBe('valid')
    expect(await ed25519Verify(pub, msg.replace('LEAK', 'NO_EVIDENCE'), sig)).toBe('invalid')
    expect(await ed25519Verify(pub, msg, sig.slice(0, -2) + (sig.endsWith('00') ? '01' : '00'))).toBe('invalid')
  })
})
