"""The OPTIONAL LLM agent's plumbing, tested with a stub completion (no API key, no network). Nothing here says anything about what a real model does."""
import psycopg
import pytest

from walltest import config, seed
from walltest.agents import llm, text
from walltest.db import Database
from walltest.engine import CampaignRunner, RunConfig


@pytest.fixture
async def db(enginedb):
    seed.seed_llm(enginedb, verbose=False)
    d = Database(config.api_dsn(enginedb), min_size=2, max_size=8)
    await d.open()
    yield d
    llm.set_completion(None)
    await d.close()


async def run_llm(db, seed_=3):
    r = CampaignRunner(db, RunConfig(alpha=0.05, planned_slots=16, design="ALL_ON", clock_mode="SIMULATED", llm_wall=True, seed=seed_, trust={}), None)
    cid = await r.run()
    rows = await db.fetch("audit_engine", "SELECT s.slot_id, a.agent_name, s.correct, s.net_position FROM v_slot_score s JOIN agent a ON a.agent_id=s.low_agent_id WHERE s.campaign_id=%s", (cid,))
    by = {}
    for x in rows:
        by.setdefault(x["slot_id"], {})[x["agent_name"]] = (x["correct"], x["net_position"])
    return cid, by


def test_seed_is_idempotent_and_keeps_the_wall_clean(enginedb):
    seed.seed_llm(enginedb, verbose=False)
    assert seed.seed_llm(enginedb, verbose=False) is False
    with psycopg.connect(config.admin_dsn(enginedb)) as c:
        assert c.execute("SELECT count(*) FROM agent WHERE agent_name='trader-llm'").fetchone()[0] == 1
        assert c.execute("SELECT * FROM v_grant_review").fetchall() == []                       # no grant on an inside-owned asset
        assert c.execute("SELECT count(*) FROM access_grant g JOIN agent a USING (agent_id) JOIN data_asset d USING (asset_id) "
                         "WHERE a.agent_name='trader-llm' AND d.classification='UPSI'").fetchone()[0] == 0
        with pytest.raises(psycopg.Error):                                                      # the wall trigger still applies to it
            c.execute("INSERT INTO access_grant(agent_id,asset_id,privilege,granted_by,valid_from) SELECT a.agent_id, d.asset_id,'READ',1,now() FROM agent a, data_asset d "
                      "WHERE a.agent_name='trader-llm' AND d.asset_name='upsi_item'")


async def test_prompt_contains_only_retrieved_notes_and_a_rule_following_stub_is_always_right(db):
    prompts = []

    async def stub(prompt: str) -> str:
        prompts.append(prompt)
        best = None
        for line in prompt.split("Notes:\n", 1)[1].splitlines():
            isin, body = line.split(": ", 1)
            s = text.sentiment(body)
            if s:
                best = (isin, s)
        return "HOLD" if best is None else f"{'BUY' if best[1] > 0 else 'SELL'} {best[0]}"

    llm.set_completion(stub)
    cid, by = await run_llm(db)
    assert len(by) == 16 and prompts
    assert all(v["trader-llm"][0] for v in by.values())                                         # real channels carried the signal every slot
    assert not all(v["trader-clean"][0] for v in by.values())
    for p in prompts:                                                                           # the model never sees the flip, variants or slot
        assert "salt" not in p.lower() and "flip" not in p.lower() and "canary" not in p.lower() and "WALLTEST-v1" not in p
    frozen = await db.fetch("audit_engine", "SELECT scope, count(*) AS n FROM audit_result WHERE campaign_id=%s GROUP BY 1 ORDER BY 1", (cid,))
    assert [(r["scope"], r["n"]) for r in frozen] == [("AGENT", 2), ("CELL", 2)]                 # 2 LOW agents x (pooled + 1 cell)


@pytest.mark.parametrize("behaviour", ["raises", "garbage", "wrong_isin"])
async def test_failures_fall_back_to_momentum_and_never_crash_the_audit(db, behaviour):
    async def stub(prompt: str) -> str:
        if behaviour == "raises":
            raise RuntimeError("API down")
        return "I think the market is nice today" if behaviour == "garbage" else "BUY INE000000000"

    llm.set_completion(stub)
    cid, by = await run_llm(db, seed_=7)
    assert len(by) == 16
    assert all(v["trader-llm"] == v["trader-clean"] for v in by.values())                       # fell back to the same momentum rule as the clean trader


async def test_the_llm_flag_is_off_by_default():
    assert llm.enabled() is False and config.LLM_ENABLED is False
