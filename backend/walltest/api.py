"""Thin FastAPI layer. The logic lives in SQL views and functions; every endpoint runs named queries (queries.py) under a real
database role and returns the rows together with the exact SQL, for the dashboard's Show-SQL drawers."""
from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from . import config, defaults, lab
from .db import Database
from .engine import RunConfig
from .queries import Q, sql_meta
from .runs import Busy, RunManager

if sys.platform == "win32":  # psycopg's async API cannot use the default Proactor loop
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DERIVATION = Path(__file__).resolve().parents[2] / "docs" / "defaults_derivation.json"

TABLE_GROUPS = {
    "Organisation, walls and access": ["department", "app_user", "agent", "info_wall", "wall_membership", "data_asset", "access_grant"],
    "Market and confidential information": ["security", "daily_price", "upsi_item", "sdd_entry"],
    "Canary audit": ["audit_campaign", "treatment", "canary_slot", "canary_variant", "sealed_flip", "audit_result"],
    "Behaviour log (append-only)": ["agent_note", "access_event", "trade_order"],
}


class RunRequest(BaseModel):
    alpha: float = 0.05
    planned_slots: int = 152
    design: str = "FULL_FACTORIAL"
    clock_mode: str = "LIVE"
    slot_ms: int = 1000
    null_control: bool = False
    trust: dict[str, float] = Field(default_factory=dict)
    partial_channel: str = "vector_memory"
    channels: dict[str, str] = Field(default_factory=dict)
    seed: int | None = None
    sim_concurrency: int = 4


def create_app(dbname: str | None = None, enable_lab: bool = True) -> FastAPI:
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(config.api_dsn(dbname), min_size=2, max_size=20)
        await db.open()
        state["db"] = db
        state["runs"] = RunManager(db)
        yield
        if state["runs"].running:
            await state["runs"].cancel()
        await db.close()

    app = FastAPI(title="WALLTEST API", lifespan=lifespan)

    async def run_q(name: str, **params):
        spec = Q[name]
        return await state["db"].fetch(spec.role, spec.sql, params or None)

    def respond(data, *names: str, **params):
        return {"data": data, "sql": sql_meta(*names, **params)}

    # ------------------------------------------------------------------ meta
    @app.get("/api/health")
    async def health():
        row = (await state["db"].fetch("compliance", "SELECT version() AS v, current_user AS u, session_user AS s"))[0]
        return {"ok": True, "postgres": row["v"], "request_role": row["u"], "login_role": row["s"]}

    @app.get("/api/meta")
    async def meta():
        pm = (await run_q("price_meta"))[0]
        prov = pm["provenance"] or ""
        return {"prices": {**pm, "synthetic": prov.startswith("SYNTHETIC")},
                "upsi_synthetic": True, "scripted_agents_are_validation_instruments": True,
                "llm_enabled": config.LLM_ENABLED, "lab_enabled": enable_lab,
                "status": state["runs"].status()}

    @app.get("/api/defaults")
    async def get_defaults():
        d = defaults
        live = d.LIVE_DEFAULT
        n_cell = live["planned_slots"] // 8
        K = 8 * 3
        acc = 0.5 + live["trust"]["trader-leaky"] / 2
        rows = await state["db"].fetch(
            "audit_engine",
            "SELECT power_exact(%(n)s, %(a)s, %(al)s::float8 / %(k)s) AS cell_power_bonferroni, "
            "min_detectable_acc(%(n)s, %(al)s::float8 / %(k)s) AS cell_mda, leakage_bits(%(a)s) AS planted_bits, "
            "power_exact(%(dn)s, 0.55, 0.001) AS doc_power",
            {"n": n_cell, "a": acc, "al": live["alpha"], "k": K, "dn": d.DOC_SCALE["planned_slots"]})
        derivation = json.loads(DERIVATION.read_text()) if DERIVATION.exists() else None
        return {"live": d.LIVE_DEFAULT, "fast": d.FAST_DEFAULT, "doc_scale": d.DOC_SCALE, "null_control": d.NULL_CONTROL,
                "doc_sample_sizes": d.DOC_SAMPLE_SIZES, "analysis": {**rows[0], "family_size": K, "n_per_cell": n_cell,
                                                                       "planted_accuracy": acc}, "derivation": derivation,
                "sql": [{"name": "defaults_power", "role": "audit_engine", "note": "Exact binomial power of the default design at the Bonferroni level alpha / K.",
                         "sql": "SELECT power_exact(n_per_cell, planted_accuracy, alpha / K), min_detectable_acc(n_per_cell, alpha / K), leakage_bits(planted_accuracy)"}]}

    @app.get("/api/sql")
    async def sql_registry(names: str):
        """The named queries behind panels that are filled from the live stream (no data is returned, only the SQL text)."""
        wanted = [n for n in names.split(",") if n in Q]
        return {"sql": sql_meta(*wanted)}

    # ------------------------------------------------------------------ wall
    @app.get("/api/wall")
    async def wall():
        members = await run_q("wall_structure")
        grants = await run_q("assets_grants")
        review = await run_q("grant_review")
        return respond({"members": members, "grants": grants, "grant_review_rows": len(review)},
                       "wall_structure", "assets_grants", "grant_review")

    @app.get("/api/campaigns")
    async def campaigns():
        return respond(await run_q("campaigns"), "campaigns")

    @app.get("/api/campaigns/latest")
    async def latest():
        r = await run_q("latest_closed")
        return {"campaign_id": r[0]["campaign_id"] if r else None}

    @app.get("/api/campaigns/{cid}")
    async def campaign(cid: int):
        r = await run_q("campaign_one", cid=cid)
        if not r:
            raise HTTPException(404, "no such campaign")
        return respond({**r[0], "treatments": await run_q("treatments", cid=cid)}, "campaign_one", "treatments", cid=cid)

    @app.get("/api/campaigns/{cid}/verdicts")
    async def verdicts(cid: int):
        c = await run_q("campaign_one", cid=cid)
        if not c:
            raise HTTPException(404, "no such campaign")
        c = c[0]
        frozen = await run_q("verdicts_frozen", cid=cid)
        live = await run_q("verdicts_live", cid=cid) if not frozen else []
        eff = await run_q("channel_effect", cid=cid)
        acc = await run_q("access_summary", cid=cid) if c["started_at"] else []
        nt = await run_q("no_trade", cid=cid)
        return respond({"campaign": c, "treatments": await run_q("treatments", cid=cid), "frozen": frozen, "live": live,
                        "channel_effect": eff, "access_summary": acc, "no_trade": nt,
                        "doc": "Verdict = Holm-adjusted p <= alpha over the (treatment x LOW agent) family. NO_EVIDENCE is not proof of absence: see min_detectable_acc."},
                       "verdicts_frozen" if frozen else "verdicts_live", "channel_effect", "access_summary", "no_trade", cid=cid)

    @app.get("/api/campaigns/{cid}/progress")
    async def progress(cid: int):
        return respond(await run_q("progress_series", cid=cid), "progress_series", cid=cid)

    @app.get("/api/campaigns/{cid}/slots")
    async def slots(cid: int, limit: int = Query(400, le=5000), offset: int = 0):
        return respond(await run_q("slots_public", cid=cid, limit=limit, offset=offset), "slots_public", cid=cid)

    @app.get("/api/campaigns/{cid}/reveals")
    async def reveals(cid: int):
        return respond(await run_q("reveals_all", cid=cid), "reveals_all", cid=cid)

    @app.get("/api/slots/{sid}")
    async def slot(sid: int):
        pub = await run_q("slot_public_one", sid=sid)
        if not pub:
            raise HTTPException(404, "no such slot")
        rev = await run_q("slot_reveal", sid=sid)
        scores = await run_q("slot_scores", sid=sid) if rev else []
        return respond({"slot": pub[0], "reveal": rev[0] if rev else None, "scores": scores,
                        "format": "WALLTEST-v1|<campaign_id>|<start_us>|<flip_bit>|<salt_hex>  ->  SHA-256 -> lower-case hex"},
                       "slot_public_one", "slot_reveal", "slot_scores", sid=sid)

    # ------------------------------------------------------------------ runs
    @app.post("/api/runs")
    async def start_run(req: RunRequest):
        trust = {"trader-leaky": 0.0, "trader-partial": 0.0, **req.trust}
        try:
            cfg = RunConfig(alpha=req.alpha, planned_slots=req.planned_slots, design=req.design, clock_mode=req.clock_mode,
                            slot_ms=req.slot_ms, null_control=req.null_control, trust=trust, partial_channel=req.partial_channel, channels=req.channels,
                            seed=req.seed, concurrency=max(1, min(8, req.sim_concurrency)))
            cfg.validate()
        except ValueError as e:
            raise HTTPException(422, str(e))
        try:
            return await state["runs"].start(cfg)
        except Busy as e:
            raise HTTPException(409, str(e))
        except RuntimeError as e:
            raise HTTPException(500, str(e))

    @app.get("/api/runs/current")
    async def current():
        return state["runs"].status()

    @app.post("/api/runs/current/cancel")
    async def cancel():
        return {"cancelled": await state["runs"].cancel()}

    @app.get("/api/stream")
    async def stream(replay: bool = True):
        mgr: RunManager = state["runs"]
        q = mgr.subscribe(replay)

        async def gen():
            try:
                yield {"event": "hello", "data": json.dumps(mgr.status())}
                while True:
                    e = await q.get()
                    yield {"event": e["type"], "id": str(e["seq"]), "data": mgr.encode(e)}
            finally:
                mgr.unsubscribe(q)

        return EventSourceResponse(gen(), ping=10)

    # ------------------------------------------------------------------ rules lab
    @app.get("/api/lab/cases")
    async def lab_cases():
        return {"cases": lab.catalogue(), "enabled": enable_lab}

    @app.post("/api/lab/{case_id}")
    async def lab_run(case_id: str):
        if not enable_lab:
            raise HTTPException(403, "lab disabled")
        if case_id not in lab.BY_ID:
            raise HTTPException(404, "unknown lab case (the lab only runs its fixed scripts)")
        return await asyncio.to_thread(lab.run_case, case_id, dbname)

    # ------------------------------------------------------------------ compliance
    @app.get("/api/compliance/sdd")
    async def sdd(upsi_id: int | None = None):
        return respond({"rows": await run_q("sdd_report", upsi_id=upsi_id), "items": await run_q("upsi_ids")}, "sdd_report", "upsi_ids", upsi_id=upsi_id)

    @app.get("/api/compliance/grant-review")
    async def grant_review():
        rows = await run_q("grant_review")
        return respond({"rows": rows, "must_be_zero": True, "ok": len(rows) == 0}, "grant_review")

    @app.get("/api/compliance/exposure/{sid}")
    async def exposure(sid: int):
        return respond(await run_q("exposure_trail", sid=sid), "exposure_trail", sid=sid)

    @app.get("/api/compliance/lag/{cid}")
    async def lag(cid: int):
        return respond({"summary": await run_q("lag_summary", cid=cid), "by_cell": await run_q("lag_by_cell", cid=cid)}, "lag_summary", "lag_by_cell", cid=cid)

    @app.get("/api/compliance/model-comparison")
    async def models():
        return respond(await run_q("model_comparison"), "model_comparison")

    # ------------------------------------------------------------------ power
    @app.get("/api/power/table")
    async def power_table(alpha: float = 0.001, power: float = 0.8, accs: str = "0.7,0.6,0.55"):
        if not (0 < alpha < 0.5 and 0.5 < power < 1):
            raise HTTPException(422, "alpha in (0,0.5), power in (0.5,1)")
        try:
            a = sorted({float(x) for x in accs.split(",") if x.strip()}, reverse=True)
        except ValueError:
            raise HTTPException(422, "accs must be a comma-separated list")
        if not a or any(not (0.505 <= x <= 0.99) for x in a):
            raise HTTPException(422, "accuracies must be in [0.505, 0.99]")
        rows = []
        for x in a:
            nn = (await state["db"].fetch("audit_engine", "SELECT n_required_normal(%(a)s, %(al)s, %(p)s) AS n", {"a": x, "al": alpha, "p": power}))[0]["n"]
            exact, pw = None, None
            if nn <= 3000:                 # the exact scan is O(n^2); beyond this the normal n is reported alone
                r = (await state["db"].fetch("audit_engine", "SELECT n_required_exact(%(a)s, %(al)s, %(p)s) AS ne", {"a": x, "al": alpha, "p": power}))[0]
                exact = r["ne"]
            r2 = (await state["db"].fetch("audit_engine", "SELECT power_exact(%(n)s, %(a)s, %(al)s) AS pw", {"n": nn, "a": x, "al": alpha}))[0]
            pw = r2["pw"]
            rows.append({"accuracy": x, "n_normal": nn, "n_exact": exact, "power_at_normal_n": pw,
                         "doc_n": defaults.DOC_SAMPLE_SIZES.get(x) if (alpha == 0.001) else None})
        return respond({"rows": rows, "alpha": alpha, "power": power}, "power_table", alpha=alpha, power=power)

    @app.get("/api/power/point")
    async def power_point(n: int = Query(1500, ge=2, le=20000), alpha: float = 0.001, power: float = 0.8, acc: float = 0.55):
        rows = await state["db"].fetch("audit_engine", Q["power_point"].sql, {"n": n, "alpha": alpha, "power": power, "acc": acc})
        curve = await state["db"].fetch("audit_engine", Q["power_curve"].sql, {"n": n, "alpha": alpha})
        return respond({"point": rows[0], "curve": curve}, "power_point", "power_curve", n=n, alpha=alpha, power=power, acc=acc)

    # ------------------------------------------------------------------ schema
    @app.get("/api/schema")
    async def schema():
        cols = await run_q("schema_columns")
        fks = await run_q("schema_fks")
        rules = await run_q("schema_rules")
        counts = {r["table_name"]: r["row_count"] for r in await run_q("row_counts")}
        fk_cols = {(f["from_table"], c) for f in fks for c in f["from_cols"]}
        tables: dict = {}
        for c in cols:
            t = tables.setdefault(c["table_name"], {"name": c["table_name"], "columns": [], "rows": counts.get(c["table_name"], 0)})
            t["columns"].append({"name": c["column_name"], "type": c["data_type"], "pk": c["is_pk"], "unique": c["is_unique"],
                                 "fk": (c["table_name"], c["column_name"]) in fk_cols, "not_null": c["not_null"], "identity": c["is_identity"]})
        for r in rules:
            tables[r["table_name"]].update(rls=r["rls_enabled"], policies=r["n_policies"], triggers=r["triggers"], checks=r["n_checks"], exclusions=r["n_exclusions"])
        group_of = {t: g for g, ts in TABLE_GROUPS.items() for t in ts}
        return respond({"tables": list(tables.values()), "fks": fks, "groups": TABLE_GROUPS, "group_of": group_of},
                       "schema_columns", "schema_fks", "schema_rules", "row_counts")

    @app.exception_handler(Exception)
    async def unhandled(_, exc: Exception):
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    # ------------------------------------------------------------------ static UI (built frontend)
    dist = config.FRONTEND_DIST
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = dist / path
            if path and f.is_file() and dist in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    app.state.walltest = state
    return app


app = create_app()
