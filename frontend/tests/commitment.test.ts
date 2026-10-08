import { describe, expect, it } from 'vitest'
import { commitmentOf, flipOneBitHex, parseIsoMicros, preimage, sha256FallbackHex, sha256Hex } from '../src/lib/commitment'

// Known-answer vector shared with backend/tests/test_commitment.py (SQL and Python assert the same bytes).
const KAT_PRE = 'WALLTEST-v1|7|1767323045123456|1|000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f'
const SALT = Array.from({ length: 32 }, (_, i) => i.toString(16).padStart(2, '0')).join('')

describe('commitment (browser)', () => {
  it('parses microseconds exactly', () => {
    expect(parseIsoMicros('2026-01-02T03:04:05.123456Z')).toBe(1767323045123456n)
    expect(parseIsoMicros('2026-01-02T03:04:05.1Z')).toBe(1767323045100000n)
    expect(parseIsoMicros('1970-01-01T00:00:00.000001Z')).toBe(1n)
  })
  it('builds the canonical preimage', () => {
    expect(preimage(7, parseIsoMicros('2026-01-02T03:04:05.123456Z'), 1, SALT)).toBe(KAT_PRE)
  })
  it('matches the Python/SQL known answer (WebCrypto and the pure-JS fallback agree)', async () => {
    const want = '7f434cb4d5dfcef6ce9b47e8019cdebc84115c763cf4ea52494e62576b6ae7bf'   // hashlib.sha256 of KAT_PRE
    const viaSubtle = await sha256Hex(KAT_PRE)
    expect(viaSubtle).toBe(sha256FallbackHex(KAT_PRE))
    expect(await commitmentOf(7, '2026-01-02T03:04:05.123456Z', 1, SALT)).toBe(viaSubtle)
    expect(viaSubtle).toBe(want)
  })
  it('fallback SHA-256 matches standard vectors', () => {
    expect(sha256FallbackHex('')).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
    expect(sha256FallbackHex('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
    expect(sha256FallbackHex('a'.repeat(1000))).toBe('41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3')
  })
  it('one flipped bit or one microsecond changes the commitment', async () => {
    const base = await commitmentOf(7, '2026-01-02T03:04:05.123456Z', 1, SALT)
    expect(await commitmentOf(7, '2026-01-02T03:04:05.123456Z', 0, SALT)).not.toBe(base)
    expect(await commitmentOf(7, '2026-01-02T03:04:05.123457Z', 1, SALT)).not.toBe(base)
    expect(await commitmentOf(7, '2026-01-02T03:04:05.123456Z', 1, flipOneBitHex(SALT))).not.toBe(base)
  })
})
