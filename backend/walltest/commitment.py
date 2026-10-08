"""Canonical commitment (see docs/COMMITMENT_FORMAT.md). Must agree byte-for-byte with SQL commitment_hash()
and the browser inspector (Web Crypto)."""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets

EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
MIN_SALT_BYTES = 16


def to_us(ts: dt.datetime) -> int:
    """Integer microseconds since the Unix epoch (exact: no float arithmetic)."""
    if ts.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return (ts - EPOCH) // dt.timedelta(microseconds=1)


def preimage(campaign_id: int, slot_start: dt.datetime, flip_bit: int, salt: bytes) -> str:
    if flip_bit not in (0, 1):
        raise ValueError("flip_bit must be 0 or 1")
    if len(salt) < MIN_SALT_BYTES:
        raise ValueError("salt must be at least 16 bytes")
    return f"WALLTEST-v1|{campaign_id}|{to_us(slot_start)}|{flip_bit}|{salt.hex()}"


def commitment(campaign_id: int, slot_start: dt.datetime, flip_bit: int, salt: bytes) -> str:
    return hashlib.sha256(preimage(campaign_id, slot_start, flip_bit, salt).encode("utf-8")).hexdigest()


def draw_flip() -> tuple[int, bytes]:
    """Coin flip and 32-byte salt from the OS CSPRNG (secrets), never from a seeded PRNG."""
    return secrets.randbits(1), secrets.token_bytes(32)
