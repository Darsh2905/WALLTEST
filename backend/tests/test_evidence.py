"""Merkle evidence over a campaign's audit trail + Ed25519-signed snapshots (migrations 013/014, docs/METHODS.md §4).

Everything is re-implemented here in plain Python (hashlib) from the canonical format, independently of the SQL:
  leaf = SHA256(0x00 || payload), node = SHA256(0x01 || left || right), odd node promoted, empty root = SHA256('')."""
import datetime as dt
import hashlib

import pytest
from conftest import as_role, dicts_as, expect_error, one

from walltest import signing

UTC = dt.timezone.utc


def H(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def py_root(leaves: list[bytes]) -> bytes:
    if not leaves:
        return H(b"")
    lvl = leaves
    while len(lvl) > 1:
        lvl = [H(b"\x01" + lvl[i] + lvl[i + 1]) if i + 1 < len(lvl) else lvl[i] for i in range(0, len(lvl), 2)]
    return lvl[0]


def py_verify(leaf: bytes, proof: list[tuple[str, str | None]], root: bytes) -> bool:
    h = leaf
    for side, sib in proof:
        if side == "L":
            h = H(b"\x01" + bytes.fromhex(sib) + h)
        elif side == "R":
            h = H(b"\x01" + h + bytes.fromhex(sib))
    return h == root


def frozen_campaign(fx, conn, n=3, freeze=True):
    """A campaign with real gateway activity: research writes notes, traders read them and trade, all inside the window."""
    from test_gateway import at, call
    cid = fx.campaign(planned=n, started_at=fx.BASE)
    slots = []
    for i in range(n):
        s = fx.slot(cid, start=fx.BASE + dt.timedelta(seconds=10 * i + 1), length=2, flip=i % 2, pos_bit=1)
        slots.append(s)
        at(conn, s["start"] + dt.timedelta(seconds=0.3))
        call(conn, "research-agent", "SELECT * FROM gw_read_canary()")
        call(conn, "research-agent", "SELECT gw_write_note('notes_table',%s,'derived note')", (s["isin"],))
        at(conn, s["start"] + dt.timedelta(seconds=0.8))
        call(conn, "trader-leaky", "SELECT * FROM gw_read_notes('notes_table',5)")
        call(conn, "trader-leaky", "SELECT gw_place_orders(ARRAY[%s],ARRAY[%s],10)", (s["isin"], "BUY" if s["direction"] == "POSITIVE" else "SELL"))
        call(conn, "trader-clean", "SELECT * FROM gw_read_prices('2025-06-30',5)")
    closed = slots[-1]["end"]
    if freeze:
        as_role(conn, "audit_engine", "SELECT freeze_campaign(%s,%s,%s)", (cid, fx.engine_user, closed))
    else:
        as_role(conn, "audit_engine", "SELECT close_campaign_window(%s,%s)", (cid, closed))
    return cid, slots


def leaves(conn, cid):
    return dicts_as(conn, "audit_engine", "SELECT * FROM evidence_leaves(%s)", (cid,))


def test_leaves_cover_every_kind_of_evidence_and_hash_canonically(fx, conn):
    cid, slots = frozen_campaign(fx, conn)
    L = leaves(conn, cid)
    kinds = [r["kind"] for r in L]
    assert kinds == sorted(kinds, key=["canary_slot", "canary_variant", "sealed_flip", "agent_note", "access_event", "trade_order"].index)
    count = {k: kinds.count(k) for k in set(kinds)}
    assert count["canary_slot"] == 3 and count["canary_variant"] == 6 and count["sealed_flip"] == 3 and count["agent_note"] == 3
    assert count["trade_order"] == 3 and count["access_event"] == one(conn, "SELECT count(*) FROM access_event")
    for r in L:
        assert bytes(r["leaf_hash"]) == H(b"\x00" + r["payload"].encode()) and r["payload"].startswith(r["kind"] + ":{")
    assert [r["ord"] for r in L] == list(range(len(L)))


def test_root_matches_an_independent_python_merkle_tree(fx, conn):
    cid, _ = frozen_campaign(fx, conn)
    L = leaves(conn, cid)
    root, n = conn.execute("SELECT root, leaves FROM evidence_root(%s)", (cid,)).fetchone()
    assert n == len(L) and root == py_root([bytes(r["leaf_hash"]) for r in L]).hex()
    frozen = conn.execute("SELECT DISTINCT evidence_root, evidence_leaves FROM audit_result WHERE campaign_id=%s", (cid,)).fetchall()
    assert frozen == [(root, n)]


@pytest.mark.parametrize("n", [0, 1, 2, 3, 5, 7, 8, 13])
def test_sql_merkle_root_equals_python_for_any_size(conn, n):
    hs = [H(bytes([i])) for i in range(n)]
    assert bytes(one(conn, "SELECT merkle_root(%s::bytea[])", (hs,))) == py_root(hs)


def test_every_inclusion_proof_verifies_and_a_wrong_leaf_does_not(fx, conn):
    cid, _ = frozen_campaign(fx, conn, n=2)
    L = leaves(conn, cid)
    assert len(L) > 8                               # deep enough for several proof levels (odd sizes: see the parametrised test)
    root =bytes.fromhex(one(conn, "SELECT root FROM evidence_root(%s)", (cid,)))
    for r in L:
        proof = [(p[1], p[2]) for p in conn.execute("SELECT level, side, sibling FROM evidence_proof(%s,%s) ORDER BY level", (cid, r["ord"]))]
        assert py_verify(bytes(r["leaf_hash"]), proof, root), r["ref"]
        assert not py_verify(H(b"\x00" + (r["payload"] + " ").encode()), proof, root)
    expect_error(conn, "SELECT * FROM evidence_proof(%s,%s)", (cid, len(L)), contains="out of range")


def test_evidence_is_independent_of_the_session_time_zone(fx, conn):
    cid, _ = frozen_campaign(fx, conn)
    root = one(conn, "SELECT root FROM evidence_root(%s)", (cid,))
    conn.execute("SET LOCAL TimeZone = 'Asia/Kolkata'")
    conn.execute("SET LOCAL extra_float_digits = 0")
    assert one(conn, "SELECT root FROM evidence_root(%s)", (cid,)) == root


@pytest.mark.parametrize("attack", ["edit_order", "delete_access_event", "backdate_note", "insert_order"])
def test_any_change_to_the_evidence_changes_the_root(fx, conn, attack):
    """A superuser can disable the append-only triggers; the recomputed root then no longer matches the signed one."""
    cid, slots = frozen_campaign(fx, conn)
    frozen_root = one(conn, "SELECT DISTINCT evidence_root FROM audit_result WHERE campaign_id=%s", (cid,))
    assert one(conn, "SELECT root FROM evidence_root(%s)", (cid,)) == frozen_root
    for t in ("trade_order", "access_event"):
        conn.execute(f"ALTER TABLE {t} DISABLE TRIGGER {t}_append_only")
    if attack == "edit_order":
        conn.execute("UPDATE trade_order SET quantity = quantity + 1 WHERE order_id = (SELECT min(order_id) FROM trade_order)")
    elif attack == "delete_access_event":
        conn.execute("DELETE FROM access_event WHERE event_id = (SELECT max(event_id) FROM access_event)")
    elif attack == "backdate_note":
        conn.execute("ALTER TABLE agent_note DISABLE TRIGGER agent_note_log")
        conn.execute("UPDATE agent_note SET created_at = created_at - interval '1 ms' WHERE note_id = (SELECT min(note_id) FROM agent_note)")
    else:   # a forged order slipped into the window after the fact
        conn.execute("INSERT INTO trade_order(agent_id,isin,side,quantity,placed_at) VALUES (%s,%s,'BUY',1,%s)",
                     (fx.agent["trader-clean"], slots[0]["isin"], slots[0]["start"] + dt.timedelta(seconds=1)))
    assert one(conn, "SELECT root FROM evidence_root(%s)", (cid,)) != frozen_root


def test_evidence_needs_a_closed_window(fx, conn):
    cid = fx.campaign(planned=1, started_at=fx.BASE)
    fx.slot(cid)
    expect_error(conn, "SELECT * FROM evidence_root(%s)", (cid,), sqlstate="WT012", role="audit_engine")


def test_slots_committed_beyond_the_window_are_not_evidence(fx, conn):
    cid, slots = frozen_campaign(fx, conn, n=3, freeze=False)
    extra = fx.slot(cid, start=slots[-1]["end"] + dt.timedelta(seconds=5))           # committed ahead, never run
    refs = {r["ref"] for r in leaves(conn, cid)}
    assert f"canary_slot:{extra['slot_id']}" not in refs and f"canary_slot:{slots[-1]['slot_id']}" in refs


def test_evidence_functions_are_not_open_to_agents(fx, conn):
    cid, _ = frozen_campaign(fx, conn)
    for role in ("low_side", "high_side"):
        expect_error(conn, "SELECT * FROM evidence_leaves(%s)", (cid,), sqlstate="42501", role=role)


# ----- signatures ---------------------------------------------------------------------------------------------------------------------
def test_ed25519_signature_over_the_snapshot(tmp_path, fx, conn):
    s = signing.Signer(tmp_path / "k.pem")
    assert (tmp_path / "k.pem").stat().st_mode & 0o777 == 0o600
    cid, _ = frozen_campaign(fx, conn, freeze=False)
    msg = one(conn, "SELECT snapshot_message(%s)", (cid,))
    sha = hashlib.sha256(msg.encode()).hexdigest()
    sig = s.sign(msg.encode())
    as_role(conn, "audit_engine", "SELECT freeze_campaign(%s,%s,clock_timestamp(),%s,%s,%s)", (cid, fx.engine_user, sha, sig, s.public_hex))
    frozen_msg = one(conn, "SELECT snapshot_message_frozen(%s)", (cid,))
    pk, sg = conn.execute("SELECT DISTINCT signer_pubkey, signature FROM audit_result WHERE campaign_id=%s", (cid,)).fetchone()
    assert signing.verify(pk, frozen_msg.encode(), sg)
    assert not signing.verify(pk, frozen_msg.replace("LEAK", "NO_EVIDENCE", 1).encode() + b" ", sg)
    assert not signing.verify(signing.Signer(tmp_path / "other.pem").public_hex, frozen_msg.encode(), sg)
    assert signing.Signer(tmp_path / "k.pem").public_hex == s.public_hex          # the key file is reused, not regenerated


def test_the_leaf_format_survives_schema_evolution(fx, conn):
    """Leaves hash an explicit column list (WALLTEST-EVIDENCE-1), so a later migration adding columns to an evidence table
    must not change any leaf -- otherwise every previously signed root would stop verifying."""
    cid, _ = frozen_campaign(fx, conn)
    root = one(conn, "SELECT root FROM evidence_root(%s)", (cid,))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")              # fire the pending deferred checks so the tables can be altered
    for t in ("canary_slot", "canary_variant", "sealed_flip", "agent_note", "access_event", "trade_order"):
        conn.execute(f"ALTER TABLE {t} ADD COLUMN test_added_later integer DEFAULT 7")
    assert one(conn, "SELECT root FROM evidence_root(%s)", (cid,)) == root
