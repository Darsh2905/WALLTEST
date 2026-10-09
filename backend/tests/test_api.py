"""The thin FastAPI layer against the real database: roles on the request path, Show-SQL on every panel, runs + SSE, errors."""
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from walltest.api import create_app


@pytest.fixture(scope="module")
def client(enginedb):
    """A REAL uvicorn server (TestClient buffers whole responses, which cannot test an endless SSE stream) + httpx."""
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    app = create_app(enginedb)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60) as c:
        c.app = app
        yield c
    server.should_exit = True
    th.join(timeout=10)


def wait_idle(client, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = client.get("/api/runs/current").json()
        if not s["running"]:
            return s
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_health_shows_the_request_path_is_not_a_superuser(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["login_role"] == "walltest_api" and h["request_role"] == "compliance" and "PostgreSQL 16" in h["postgres"]


def test_low_side_cannot_read_upsi_item_through_the_api_path(client):
    """HTTP -> API -> pool (walltest_api) -> SET LOCAL ROLE low_side -> SELECT upsi_item."""
    r = client.post("/api/lab/low_side_select_upsi").json()
    s0 = r["steps"][0]
    assert s0["role"] == "low_side" and s0["ok"] is False and s0["error"]["sqlstate"] == "42501"
    assert "permission denied for table upsi_item" in s0["error"]["message"]
    assert r["rolled_back"] is True


def test_there_is_no_free_form_sql_surface(client):
    paths = {r.path for r in client.app.routes}
    assert not any("sql" in p.lower() and "query" in p.lower() for p in paths)
    for route in client.app.routes:
        params = getattr(route, "param_convertors", {})
        assert "sql" not in params
    assert client.post("/api/lab/drop_everything").status_code == 404
    assert client.post("/api/lab/..%2f..%2fetc").status_code in (404, 405, 422)


def test_meta_badges(client):
    m = client.get("/api/meta").json()
    assert m["prices"]["synthetic"] is False and m["prices"]["n_securities"] == 8 and m["prices"]["n_bars"] >= 4000
    assert m["upsi_synthetic"] and m["scripted_agents_are_validation_instruments"] and "REAL" in m["prices"]["provenance"]


def test_defaults_come_from_the_database(client):
    d = client.get("/api/defaults").json()
    a = d["analysis"]
    assert d["live"]["planned_slots"] == 152 and a["family_size"] == 24 and a["n_per_cell"] == 19
    assert a["cell_power_bonferroni"] == pytest.approx(0.933, abs=0.002) and a["planted_accuracy"] == pytest.approx(0.95)
    assert d["derivation"]["chosen_m"] == 19 and d["derivation"]["default_joint_power"] >= 0.8 and d["derivation"]["default_fwer"] <= 0.05


def test_every_panel_endpoint_returns_its_sql(client):
    for path in ["/api/wall", "/api/campaigns", "/api/schema", "/api/compliance/sdd", "/api/compliance/grant-review",
                 "/api/compliance/model-comparison", "/api/power/table", "/api/power/point?n=100&alpha=0.05&acc=0.7"]:
        j = client.get(path).json()
        assert j["sql"] and all(q["sql"].lstrip().upper().startswith(("SELECT", "WITH")) and q["role"] in ("compliance", "audit_engine") for q in j["sql"]), path


def test_schema_endpoint_is_the_live_catalog(client):
    d = client.get("/api/schema").json()["data"]
    assert len(d["tables"]) == 20 and len(d["fks"]) >= 30
    t = {x["name"]: x for x in d["tables"]}
    assert t["daily_price"]["rows"] >= 4000 and t["upsi_item"]["rls"] and t["sealed_flip"]["rls"] and t["canary_variant"]["rls"]
    assert any("append_only" in x for x in t["trade_order"]["triggers"]) and t["canary_slot"]["exclusions"] == 2
    assert {c["name"] for c in t["audit_result"]["columns"]} >= {"p_value", "p_adjusted", "leakage_bits", "verdict", "result_hash"}
    assert set(d["group_of"].values()) == set(d["groups"])
    pk = [c["name"] for c in t["daily_price"]["columns"] if c["pk"]]
    assert pk == ["isin", "trade_date"]


def test_power_table_reproduces_the_proposal(client):
    d = client.get("/api/power/table?alpha=0.001&power=0.8&accs=0.7,0.6,0.55").json()["data"]
    got = {r["accuracy"]: (r["doc_n"], r["n_normal"], r["n_exact"]) for r in d["rows"]}
    assert got == {0.7: (94, 94, 95), 0.6: (384, 384, 386), 0.55: (1543, 1543, 1551)}
    assert client.get("/api/power/table?alpha=0.9").status_code == 422
    assert client.get("/api/power/table?accs=0.2").status_code == 422


def test_grant_review_is_zero(client):
    d = client.get("/api/compliance/grant-review").json()["data"]
    assert d["rows"] == [] and d["ok"] is True


def test_run_validation_and_errors(client):
    assert client.post("/api/runs", json={"planned_slots": 7, "design": "FULL_FACTORIAL", "clock_mode": "SIMULATED"}).status_code == 422
    assert client.post("/api/runs", json={"alpha": 0.7, "planned_slots": 8, "design": "ALL_ON", "clock_mode": "SIMULATED"}).status_code == 422
    assert client.post("/api/runs", json={"planned_slots": 8, "design": "NOPE", "clock_mode": "SIMULATED"}).status_code == 422
    assert client.get("/api/campaigns/99999").status_code == 404
    assert client.get("/api/slots/99999").status_code == 404


def test_full_simulated_run_through_the_api_with_sse(client):
    r = client.post("/api/runs", json={"alpha": 0.05, "planned_slots": 16, "design": "ALL_ON", "clock_mode": "SIMULATED",
                                       "trust": {"trader-leaky": 1.0, "trader-partial": 1.0}})
    assert r.status_code == 200
    cid = r.json()["campaign_id"]
    # a second run while one is active is refused
    st = client.get("/api/runs/current").json()
    wait_idle(client)
    assert st["campaign_id"] == cid
    # SSE replay carries the whole run
    seen = []
    with client.stream("GET", "/api/stream") as s:
        ev = None
        for line in s.iter_lines():
            if line.startswith("event:"):
                ev = line.split(":", 1)[1].strip(); seen.append(ev)
            if ev == "run_finished":
                break
    assert seen[0] == "hello" and "campaign_created" in seen and seen.count("slot_committed") == 16 and seen.count("slot_revealed") == 16
    assert seen.index("slot_committed") < seen.index("slot_open") < seen.index("slot_revealed") and "verdict_frozen" in seen
    # frozen verdicts, with hash integrity, descriptive series and slot inspector data
    v = client.get(f"/api/campaigns/{cid}/verdicts").json()["data"]
    by = {r["low_agent"]: r for r in v["frozen"]}
    assert by["trader-leaky"]["verdict"] == "LEAK" and by["trader-partial"]["verdict"] == "LEAK" and by["trader-leaky"]["n_correct"] == 16
    assert all(r["hash_ok"] for r in v["frozen"]) and v["live"] == []
    assert by["trader-leaky"]["acc_lower"] > 0.5 and by["trader-leaky"]["leakage_bits_lower"] > 0
    slots = client.get(f"/api/campaigns/{cid}/slots").json()["data"]
    assert len(slots) == 16 and all(s["state"] == "REVEALABLE" and s["commitment_ok"] for s in slots)
    one = client.get(f"/api/slots/{slots[0]['slot_id']}").json()["data"]
    assert one["reveal"]["commitment_ok"] and one["reveal"]["preimage"].startswith("WALLTEST-v1|") and len(one["scores"]) == 3
    assert len(client.get(f"/api/campaigns/{cid}/reveals").json()["data"]) == 16
    assert len(client.get(f"/api/campaigns/{cid}/progress").json()["data"]) == 48
    tr = client.get(f"/api/compliance/exposure/{slots[0]['slot_id']}").json()["data"]
    assert tr[0]["action"] == "READ canary variant"
    assert client.get("/api/campaigns/latest").json()["campaign_id"] == cid


def test_concurrent_start_is_refused_and_cancel_works(client):
    r = client.post("/api/runs", json={"planned_slots": 8, "design": "ALL_ON", "clock_mode": "LIVE", "slot_ms": 800, "trust": {}})
    assert r.status_code == 200
    assert client.post("/api/runs", json={"planned_slots": 8, "design": "ALL_ON", "clock_mode": "LIVE", "slot_ms": 800}).status_code == 409
    time.sleep(2.5)
    assert client.post("/api/runs/current/cancel").json()["cancelled"] is True
    cid = r.json()["campaign_id"]
    assert client.get(f"/api/campaigns/{cid}").json()["data"]["status"] == "ABORTED"
    v = client.get(f"/api/campaigns/{cid}/verdicts").json()["data"]
    assert v["frozen"] == [] and all(x["verdict"] is None and x["p_raw"] is None for x in v["live"])        # no peeking over the API either


def test_custom_channel_design(client):
    r = client.post("/api/runs", json={"planned_slots": 16, "design": "CUSTOM", "clock_mode": "SIMULATED",
                                       "channels": {"vector_memory": "vary", "notes_table": "off", "cache": "on"}, "trust": {"trader-leaky": 1.0, "trader-partial": 1.0}})
    assert r.status_code == 200
    wait_idle(client)
    t = client.get(f"/api/campaigns/{r.json()['campaign_id']}").json()["data"]["treatments"]
    assert sorted((x["vector_memory_on"], x["notes_table_on"], x["cache_on"]) for x in t) == [(False, False, True), (True, False, True)]


# ----- v2 -----------------------------------------------------------------------------------------------------------------------
def _root(hashes):
    import hashlib
    lvl = [bytes.fromhex(h) for h in hashes]
    if not lvl:
        return hashlib.sha256(b"").hexdigest()
    while len(lvl) > 1:
        lvl = [hashlib.sha256(b"\x01" + lvl[i] + lvl[i + 1]).digest() if i + 1 < len(lvl) else lvl[i] for i in range(0, len(lvl), 2)]
    return lvl[0].hex()


def test_v2_hierarchical_verdicts_signed_snapshot_and_evidence_verify_from_the_api_alone(client):
    import hashlib
    from walltest import signing
    r = client.post("/api/runs", json={"alpha": 0.05, "planned_slots": 32, "design": "FULL_FACTORIAL", "clock_mode": "SIMULATED",
                                       "trust": {"trader-leaky": 1.0, "trader-partial": 1.0}})
    assert r.status_code == 200
    cid = r.json()["campaign_id"]
    wait_idle(client)
    v = client.get(f"/api/campaigns/{cid}/verdicts").json()
    d = v["data"]
    assert d["is_frozen"] and not d["legacy_v1"] and d["inference"] == "FIXED"
    assert len(d["agents"]) == 3 and len(d["cells"]) == 24 and len(d["channels"]) == 9 and d["frozen"] == d["cells"]
    ag = {x["low_agent"]: x for x in d["agents"]}
    assert ag["trader-leaky"]["verdict"] == "LEAK" and ag["trader-leaky"]["gate_passed"] and ag["trader-clean"]["verdict"] == "NO_EVIDENCE"
    assert d["wall"]["verdict"] == "LEAK" and d["wall"]["agents_flagged"] >= 1 and d["wall"]["agents"] == 3
    assert all(x["hash_ok"] for x in d["agents"] + d["cells"] + d["channels"])
    assert {s["name"] for s in v["sql"]} >= {"verdicts_frozen", "wall_verdict"}
    # signed snapshot: the server's checks, then the same checks done here from the response alone
    snap = client.get(f"/api/campaigns/{cid}/snapshot").json()["data"]
    assert all(snap["server_checks"].values()), snap["server_checks"]
    msg = snap["message"].encode()
    assert hashlib.sha256(msg).hexdigest() == snap["snapshot_sha256"] and signing.verify(snap["signer_pubkey"], msg, snap["signature"])
    assert snap["signer_pubkey"] == snap["engine_pubkey"] and snap["message"].startswith(f"WALLTEST-SNAPSHOT-v2|{cid}|{snap['evidence_root']}|")
    # evidence: rebuild the Merkle root from the leaf hashes, and verify inclusion proofs of leaves of every kind
    leaves = client.get(f"/api/campaigns/{cid}/evidence").json()["data"]["leaves"]
    assert len(leaves) == snap["evidence_leaves"] and _root([x["leaf_hash"] for x in leaves]) == snap["evidence_root"]
    for kind in ("canary_slot", "canary_variant", "sealed_flip", "agent_note", "access_event", "trade_order"):
        idx = next(x["ord"] for x in leaves if x["kind"] == kind)
        p = client.get(f"/api/campaigns/{cid}/evidence/{idx}").json()["data"]
        h = hashlib.sha256(b"\x00" + p["leaf"]["payload"].encode()).digest()
        assert h.hex() == p["leaf"]["leaf_hash"]
        for step in p["proof"]:
            if step["side"] == "L":
                h = hashlib.sha256(b"\x01" + bytes.fromhex(step["sibling"]) + h).digest()
            elif step["side"] == "R":
                h = hashlib.sha256(b"\x01" + h + bytes.fromhex(step["sibling"])).digest()
        assert h.hex() == snap["evidence_root"], kind
    assert client.get(f"/api/campaigns/{cid}/evidence/{len(leaves)}").status_code == 404
    # the peeking demonstration: anytime p-values are never below the fixed-n p for the same data
    pk = client.get(f"/api/campaigns/{cid}/peeking").json()["data"]
    assert len(pk["rows"]) == 3 * 4 and all(x["p_anytime"] >= x["p_fixed_n"] - 1e-15 for x in pk["rows"])


def test_v2_sequential_run_stops_early_through_the_api(client):
    bad = client.post("/api/runs", json={"planned_slots": 16, "design": "ALL_ON", "clock_mode": "SIMULATED", "stop_rule": "FIRST_LEAK"})
    assert bad.status_code == 422 and "SEQUENTIAL" in bad.json()["detail"]
    r = client.post("/api/runs", json={"alpha": 0.05, "planned_slots": 160, "design": "FULL_FACTORIAL", "clock_mode": "SIMULATED",
                                       "inference": "SEQUENTIAL", "stop_rule": "FIRST_LEAK", "trust": {"trader-leaky": 1.0, "trader-partial": 1.0}})
    assert r.status_code == 200
    cid = r.json()["campaign_id"]
    wait_idle(client)
    d = client.get(f"/api/campaigns/{cid}/verdicts").json()["data"]
    assert d["inference"] == "SEQUENTIAL" and all(x["method"] == "ANYTIME_EVALUE_GATEKEEPING_v2" for x in d["agents"])
    assert d["agents"][0]["n_slots"] < 160 and d["agents"][0]["log_e"] is not None


def test_v2_semantic_search_and_benchmarks(client):
    r = client.post("/api/compliance/semantic-search", json={"text": "profit well above consensus", "k": 5})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["exact"] and len(d["rows"]) <= 5 and all({"note_id", "author", "asset", "similarity"} <= set(x) for x in d["rows"])
    assert [x["similarity"] for x in d["rows"]] == sorted((x["similarity"] for x in d["rows"]), reverse=True)
    assert client.post("/api/compliance/semantic-search", json={"text": "x", "k": 5}).status_code == 422
    b = client.get("/api/benchmarks").json()["benchmarks"]
    assert "v1-baseline" in b and "v2" in b and "ab-engine-v1-v2" in b
