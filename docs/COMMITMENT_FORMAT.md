# Canonical commitment format

One byte format, used identically in **SQL** (`commitment_hash()`, migration 005), the **audit engine** (`backend/walltest/commitment.py`) and the
**browser** (`frontend/src/lib/commitment.ts`, Web Crypto with a pure-JS fallback).

```
preimage   = "WALLTEST-v1|" campaign_id "|" start_us "|" flip_bit "|" salt_hex          (ASCII string)
commitment = lower-case hex( SHA-256( UTF-8 bytes of preimage ) )                        (64 characters)
```

| field | definition |
|---|---|
| `campaign_id` | decimal integer, no padding |
| `start_us` | `lower(slot_period)` as integer **microseconds** since 1970-01-01T00:00:00Z, decimal, exact (no floating point) |
| `flip_bit` | `0` or `1` |
| `salt_hex` | lower-case hex of the raw salt bytes; at least 16 bytes (the engine draws 32 from the OS CSPRNG, `secrets.token_bytes`) |

Why microseconds: the reference implementation truncated the start time to whole seconds. Live slots last about 1–2 s, so two slots could share a
start second; microsecond resolution also makes every (campaign, start) unique (the exclusion constraint forbids overlapping slots).
JavaScript `Date` only holds milliseconds, so the browser parses the ISO string returned by `iso_us()` (`YYYY-MM-DDTHH:MM:SS.ffffffZ`) itself.

## Known-answer vector (asserted in SQL, Python and the browser)

```
campaign_id = 7
slot start  = 2026-01-02T03:04:05.123456Z      -> start_us = 1767323045123456
flip_bit    = 1
salt        = 0x000102…1f  (32 bytes)
preimage    = WALLTEST-v1|7|1767323045123456|1|000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
commitment  = 7f434cb4d5dfcef6ce9b47e8019cdebc84115c763cf4ea52494e62576b6ae7bf
```
Tests: `backend/tests/test_commitment.py` (Python + SQL, plus 300 random inputs SQL vs Python) and `frontend/tests/commitment.test.ts` (WebCrypto and the fallback).

## Protocol
1. **Commit.** Before the slot opens the engine draws `flip_bit` and `salt`, computes the commitment, and inserts the slot (commitment published) and the sealed flip in one transaction.
   The `BEFORE INSERT` trigger on `sealed_flip` recomputes the hash in SQL and rejects a mismatch (`WT002`). The trigger on `canary_slot` requires `committed_at < lower(slot_period)` (`WT004`);
   for LIVE campaigns `committed_at` is stamped by the server clock and cannot be supplied.
2. **Seal.** Row-level security hides `sealed_flip` from everyone except `audit_engine`, and from `audit_engine` until `upper(slot_period)` has passed.
3. **Reveal.** After the slot ends the engine reads the flip, and the view `v_slot_reveal` recomputes the hash (`commitment_ok`). The inspector page re-hashes every slot in the browser.
4. **Simulated clock.** In SIMULATED campaigns the engine supplies back-dated `committed_at` values; the hash check is equally strong but the wall-clock ordering cannot be proven, so the UI labels these campaigns "simulated clock".
