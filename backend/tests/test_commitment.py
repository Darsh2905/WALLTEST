"""Commit-reveal: one canonical byte format, identical in SQL, Python (engine) and the browser (Web Crypto)."""
import calendar
import datetime as dt
import hashlib
import secrets

import pytest
from conftest import expect_error, one

from walltest import commitment as cm

UTC = dt.timezone.utc
# Known-answer vector, also asserted by the browser test (frontend/tests/commitment.test.ts)
KAT_CAMPAIGN = 7
KAT_START = dt.datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
KAT_FLIP = 1
KAT_SALT = bytes(range(32))
KAT_PREIMAGE = "WALLTEST-v1|7|1767323045123456|1|000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
KAT_HASH = hashlib.sha256(KAT_PREIMAGE.encode()).hexdigest()


def test_known_answer_vector_python(conn):
    assert calendar.timegm(KAT_START.timetuple()) * 1_000_000 + 123456 == 1767323045123456
    assert cm.preimage(KAT_CAMPAIGN, KAT_START, KAT_FLIP, KAT_SALT) == KAT_PREIMAGE
    assert cm.commitment(KAT_CAMPAIGN, KAT_START, KAT_FLIP, KAT_SALT) == KAT_HASH


def test_known_answer_vector_sql(conn):
    assert one(conn, "SELECT commitment_preimage(%s,%s,%s::smallint,%s)", (KAT_CAMPAIGN, KAT_START, KAT_FLIP, KAT_SALT)) == KAT_PREIMAGE
    assert one(conn, "SELECT commitment_hash(%s,%s,%s::smallint,%s)", (KAT_CAMPAIGN, KAT_START, KAT_FLIP, KAT_SALT)).strip() == KAT_HASH


def test_sql_and_python_agree_on_random_inputs(conn):
    for _ in range(300):
        cid = secrets.randbelow(10**6) + 1
        start = dt.datetime(2020, 1, 1, tzinfo=UTC) + dt.timedelta(microseconds=secrets.randbelow(10**15))
        flip, salt = cm.draw_flip()
        salt = secrets.token_bytes(16 + secrets.randbelow(40))
        assert cm.commitment(cid, start, flip, salt) == one(conn, "SELECT commitment_hash(%s,%s,%s::smallint,%s)", (cid, start, flip, salt)).strip()


def test_microsecond_resolution_matters(conn):
    """The reference truncated start to whole seconds; slots last ~1-2 s, so two slots within one second would collide."""
    a = cm.commitment(1, KAT_START, 0, KAT_SALT)
    b = cm.commitment(1, KAT_START + dt.timedelta(microseconds=1), 0, KAT_SALT)
    c = cm.commitment(1, KAT_START.replace(microsecond=0), 0, KAT_SALT)
    assert len({a, b, c}) == 3


def test_python_rejects_short_salt_and_bad_flip():
    with pytest.raises(ValueError):
        cm.preimage(1, KAT_START, 0, b"short")
    with pytest.raises(ValueError):
        cm.preimage(1, KAT_START, 2, KAT_SALT)
    with pytest.raises(ValueError):
        cm.to_us(dt.datetime(2026, 1, 1))     # naive timestamps are refused


def test_draw_flip_uses_csprng_and_fresh_salts():
    draws = [cm.draw_flip() for _ in range(2000)]
    assert all(len(s) == 32 for _, s in draws) and len({s for _, s in draws}) == 2000
    ones = sum(f for f, _ in draws)
    assert 900 < ones < 1100          # fair coin, loose bound (sd ~22)
    import inspect
    assert "secrets" in inspect.getsource(cm.draw_flip) and "random." not in inspect.getsource(cm.draw_flip)


# ----- the commitment trigger -----------------------------------------------------------------------------------------
def test_matching_flip_is_accepted(fx):
    cid = fx.campaign()
    s = fx.slot(cid)
    assert s["slot_id"] > 0


@pytest.mark.parametrize("mutate", ["flip", "salt", "campaign", "start_by_1_microsecond"])
def test_flip_not_matching_commitment_is_rejected(fx, conn, mutate):
    """Publish a commitment computed from slightly different inputs, then try to seal the real flip: the trigger refuses."""
    cid = fx.campaign()
    start = dt.datetime(2020, 9, 1, tzinfo=UTC)
    flip, salt = 0, secrets.token_bytes(32)
    wrong = {"flip": (cid, start, 1, salt),
             "salt": (cid, start, flip, salt[:-1] + bytes([salt[-1] ^ 1])),
             "campaign": (cid + 1, start, flip, salt),
             "start_by_1_microsecond": (cid, start + dt.timedelta(microseconds=1), flip, salt)}[mutate]
    with pytest.raises(Exception) as ei:
        with conn.transaction():
            fx.slot(cid, start=start, flip=flip, salt=salt, commitment=cm.commitment(*wrong))
    assert ei.value.sqlstate == "WT002" and "does not match the commitment" in str(ei.value)


def test_commitment_mismatch_message(fx, conn):
    cid = fx.campaign()
    with pytest.raises(Exception) as ei:
        fx.slot(cid, flip=0, salt=b"s" * 32, commitment="0" * 64)
    assert getattr(ei.value, "sqlstate", None) == "WT002" and "does not match the commitment" in str(ei.value)


def test_commit_must_precede_slot_open_simulated(fx, conn):
    cid = fx.campaign()
    start = dt.datetime(2020, 9, 1, tzinfo=UTC)
    for late in (start, start + dt.timedelta(seconds=1)):
        with pytest.raises(Exception) as ei:
            with conn.transaction():
                fx.slot(cid, start=start, committed_at=late)
        assert ei.value.sqlstate == "WT004" and "commit-before-expose" in str(ei.value)


def test_live_campaign_stamps_committed_at_from_the_server_clock(fx, conn):
    cid = fx.campaign(clock="LIVE")
    start = one(conn, "SELECT clock_timestamp() + interval '5 seconds'")
    s = fx.slot(cid, start=start, committed_at=dt.datetime(2000, 1, 1, tzinfo=UTC))      # a back-dated claim must be ignored
    committed = one(conn, "SELECT committed_at FROM canary_slot WHERE slot_id=%s", (s["slot_id"],))
    assert committed > dt.datetime.now(UTC) - dt.timedelta(minutes=1) and committed < start


def test_live_slot_that_already_opened_cannot_be_committed(fx, conn):
    cid = fx.campaign(clock="LIVE")
    start = one(conn, "SELECT clock_timestamp() - interval '1 second'")
    with pytest.raises(Exception) as ei:
        with conn.transaction():
            fx.slot(cid, start=start)
    assert ei.value.sqlstate == "WT004"


def test_slots_only_while_running(fx, conn):
    cid = fx.campaign(start=False)
    with pytest.raises(Exception) as ei:
        with conn.transaction():
            fx.slot(cid)
    assert ei.value.sqlstate == "WT007"
