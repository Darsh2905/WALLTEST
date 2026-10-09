"""Drives the UI like a grader would. Marked e2e: `make e2e` (the live campaign alone takes ~2.7 minutes)."""
import json
import re
import time
import urllib.request

import pytest
from playwright.sync_api import expect

from conftest import SHOTS

pytestmark = pytest.mark.e2e
PAGES = [("01-wall", "/"), ("02-run", "/run"), ("03-verdicts", "/verdicts"), ("04-inspector", "/inspector"), ("05-lab", "/lab"),
         ("06-compliance", "/compliance"), ("07-power", "/power"), ("08-schema", "/schema"), ("09-performance", "/performance")]


def api(base, path):
    return json.load(urllib.request.urlopen(base + path))


def goto(page, path):
    page.goto(page.base + path)
    page.wait_for_load_state("networkidle")


# ----- static pages ---------------------------------------------------------------------------------------------------------------
def test_honesty_badges_on_every_page(page):
    for _, path in PAGES:
        goto(page, path)
        expect(page.get_by_text(re.compile("REAL NIFTY 50 EOD"))).to_be_visible()
        expect(page.get_by_text("synthetic UPSI & canaries")).to_be_visible()
        expect(page.get_by_text("scripted agents = validation instruments")).to_be_visible()
    assert page.errors == []


def test_power_page_recomputes_the_proposals_table(page):
    goto(page, "/power")
    t = page.locator("[data-testid=power-table]")
    expect(t.locator("tr[data-acc='0.7']")).to_contain_text("94")
    expect(t.locator("tr[data-acc='0.6']")).to_contain_text("384")
    expect(t.locator("tr[data-acc='0.55']")).to_contain_text("1,543")
    expect(t.locator("tr[data-acc='0.55']")).to_contain_text("1,551")             # exact binomial
    expect(page.get_by_text("reproduces the proposal")).to_be_visible()
    page.select_option("[data-testid=power-alpha]", "0.05")                       # interactive: the numbers change
    expect(t.locator("tr[data-acc='0.7']")).not_to_contain_text("95")
    assert page.errors == []


def test_rules_lab_shows_real_postgres_errors(page):
    goto(page, "/lab")
    expected = {"update_trade_order": "append-only", "delete_access_event": "append-only", "grant_upsi_to_low": "wall violation",
                "overlapping_slot": "canary_slot_no_overlap", "flip_mismatch": "does not match the commitment",
                "sdd_two_sharers": "sdd_exactly_one_sharer", "low_side_select_upsi": "permission denied for table upsi_item",
                "engine_reads_unended_flip": "sealed until the slot has ended", "commit_after_open": "commit-before-expose",
                "freeze_early": "no peeking"}
    for case, text in expected.items():
        page.click(f"[data-testid=lab-run-{case}]")
        expect(page.locator("[data-testid=lab-result]")).to_contain_text(text, timeout=15000)
        expect(page.get_by_text("rolled back").first).to_be_visible()
    page.click("[data-testid=lab-run-low_side_select_upsi]")
    expect(page.locator("[data-testid=lab-result]")).to_contain_text("row-level security filters silently")
    assert page.locator("textarea").count() == 0 and page.locator("input[type=text]").count() == 0       # no free-form SQL box
    assert page.errors == []


def test_every_panel_has_a_show_sql_drawer(page, live):
    """Needs data on every page (empty states have no panels), hence after the live campaign. The Performance page is the one
    exception by design: it shows benchmark FILES written by scripts/ (bench.py, ab_engine.py, index_studies.py), not queries."""
    for name, path in PAGES:
        if name == "09-performance":
            continue
        goto(page, path)
        btn = page.locator("[data-testid=show-sql]").first
        expect(btn).to_be_visible()
        btn.click()
        dlg = page.get_by_role("dialog")
        expect(dlg).to_contain_text(re.compile(r"SELECT|INSERT", re.I))
        expect(dlg).to_contain_text("SET ROLE")
        page.keyboard.press("Escape")
        expect(dlg).to_have_count(0)


def test_theme_toggle_and_persistence(page):
    goto(page, "/")
    start = page.evaluate("document.documentElement.dataset.theme")
    page.get_by_role("button", name=re.compile("Switch to")).click()
    now = page.evaluate("document.documentElement.dataset.theme")
    assert now != start
    page.reload()
    assert page.evaluate("document.documentElement.dataset.theme") == now


def test_schema_page_lists_twenty_tables(page):
    goto(page, "/schema")
    expect(page.locator("[data-testid=er] g[data-table]")).to_have_count(20)
    expect(page.locator("[data-testid=row-counts] tr")).to_have_count(20)
    page.locator("[data-testid=er] g[data-table='sealed_flip']").click()
    expect(page.get_by_text("RLS", exact=False).first).to_be_visible()


# ----- the live campaign ---------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def live(browser, base_url):
    """Run the DEFAULT live demo campaign through the UI (152 slots, 2x2x2 factorial, alpha 0.05) and keep the page for assertions."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.base = base_url
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
    goto(page, "/")
    obs = {"pulses": 0, "gate_off_seen": False, "denied_pulse": False, "withheld_during_run": True, "timeline_states": set()}
    page.click("[data-testid=run-live]")
    expect(page.get_by_role("button", name="Stop run")).to_be_visible(timeout=15000)
    t0 = time.time()
    shot_taken = False
    while time.time() - t0 < 330:
        obs["pulses"] = max(obs["pulses"], page.locator("[data-pulse]").count())
        obs["denied_pulse"] |= page.locator("[data-pulse][data-denied='1']").count() > 0
        obs["gate_off_seen"] |= page.locator("[data-gate][data-off='1']").count() > 0
        for st in page.locator("[data-slot-state]").evaluate_all("els => els.map(e => e.getAttribute('data-slot-state'))"):
            obs["timeline_states"].add(st)
        if page.get_by_role("button", name="Run live audit").is_visible():
            break
        if not shot_taken and time.time() - t0 > 20:
            SHOTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SHOTS / "live-run-wall-1440x900.png"))
            shot_taken = True
        # while running, the verdict cards must be withheld (no peeking)
        obs["withheld_during_run"] &= page.locator("[data-testid^=verdict-card-][data-verdict='LEAK']").count() == 0
        page.wait_for_timeout(250)
    else:
        raise AssertionError("campaign did not finish in time")
    page.wait_for_timeout(2500)
    yield page, obs
    ctx.close()


def test_live_run_shows_real_traffic_and_withholds_the_verdict(live):
    page, obs = live
    assert obs["pulses"] > 0, "no access_event ever crossed a gate on screen"
    assert obs["gate_off_seen"], "factorial cells with a channel off never showed a hatched gate"
    assert obs["denied_pulse"], "blocked attempts never stopped at a gate"
    assert obs["withheld_during_run"], "a LEAK verdict was visible before the planned n"
    assert {"committed", "open", "revealed"} <= obs["timeline_states"], obs["timeline_states"]


def test_headline_verdicts_leaky_leak_clean_no_evidence(live):
    page, _ = live
    goto(page, "/")
    expect(page.locator("[data-testid=verdict-card-trader-leaky]")).to_have_attribute("data-verdict", "LEAK")
    expect(page.locator("[data-testid=verdict-card-trader-clean]")).to_have_attribute("data-verdict", "NO_EVIDENCE")
    expect(page.locator("[data-testid=verdict-card-trader-partial]")).to_have_attribute("data-verdict", "LEAK")   # headline = all channels on
    expect(page.locator("[data-testid=verdict-card-trader-clean]")).to_contain_text("Not proof of absence")
    assert page.errors == []


def test_verdict_statistics_page(live):
    page, _ = live
    goto(page, "/verdicts")
    expect(page.locator("[data-testid=campaign-meta]")).to_contain_text("slots 152")
    expect(page.locator("[data-testid=campaign-meta]")).to_contain_text("row hashes verified")
    expect(page.locator("[data-testid=wall-verdict]")).to_have_attribute("data-verdict", "LEAK")
    # step 1: the gate, one pooled test per agent
    agents = page.locator("[data-testid=agent-table] tbody tr")
    assert agents.count() == 3
    assert page.locator("[data-testid=agent-table] tr[data-agent='trader-leaky']").get_attribute("data-verdict") == "LEAK"
    assert page.locator("[data-testid=agent-table] tr[data-agent='trader-partial']").get_attribute("data-verdict") == "LEAK"
    assert page.locator("[data-testid=agent-table] tr[data-agent='trader-clean']").get_attribute("data-verdict") == "NO_EVIDENCE"
    # step 2: cells (24 = 8 cells x 3 agents)
    cells = page.locator("[data-testid=verdict-table]")
    assert cells.locator("tbody tr").count() == 24
    assert cells.locator("tr[data-agent='trader-clean'][data-verdict='NO_EVIDENCE']").count() == 8
    leaky_leak = cells.locator("tr[data-agent='trader-leaky'][data-verdict='LEAK']").count()
    assert leaky_leak >= 5, f"leaky flagged in only {leaky_leak}/7 channel-on cells"
    assert cells.locator("tr[data-agent='trader-leaky'][data-verdict='NO_EVIDENCE']").count() >= 1       # all-off cell
    # step 2: channels, by the matched-pair sign test: trader-partial is attributed to vector memory and nothing else
    ch = page.locator("[data-testid=channel-table]")
    assert ch.locator("tbody tr").count() == 9
    assert ch.locator("tr[data-channel='trader-partial:vector_memory']").get_attribute("data-verdict") == "LEAK"
    for c in ("notes_table", "cache"):
        assert ch.locator(f"tr[data-channel='trader-partial:{c}']").get_attribute("data-verdict") == "NO_EVIDENCE"
    assert ch.locator("tr[data-agent='trader-clean'][data-verdict='LEAK']").count() == 0


def test_signed_evidence_verifies_in_the_browser_and_a_tampered_verdict_fails(live):
    page, _ = live
    goto(page, "/verdicts")
    page.click("[data-testid=verify-evidence]")
    checks = page.locator("[data-testid=evidence-checks]")
    for k in ("sha", "sig", "root"):
        expect(checks.locator(f"[data-check={k}]")).to_have_attribute("data-state", "ok", timeout=30000)
    page.click("[data-testid=tamper-evidence]")
    expect(checks.locator("[data-check=sha]")).to_have_attribute("data-state", "fail", timeout=15000)
    expect(checks.locator("[data-check=sig]")).to_have_attribute("data-state", "fail", timeout=15000)
    expect(checks.locator("[data-check=root]")).to_have_attribute("data-state", "ok", timeout=30000)     # the rows themselves are untouched
    page.click("[data-testid=prove-leaf]")
    expect(page.locator("[data-testid=proof-result]")).to_have_attribute("data-ok", "1", timeout=15000)
    assert page.errors == []


def test_partial_agents_leak_is_attributed_to_its_channel(live):
    page, _ = live
    goto(page, "/verdicts")
    g = page.locator("[data-testid=grid-trader-partial]")
    expect(g).to_be_visible()
    leak_tiles = g.locator("[data-cell-verdict=LEAK]")
    assert leak_tiles.count() >= 3                                                  # of the four vector-on cells
    eff = {}
    for ch in ("vector_memory", "notes_table", "cache"):
        txt = g.locator(f"[data-effect='trader-partial:{ch}']").inner_text()
        eff[ch] = float(re.findall(r"[+-]?\d+\.\d+", txt)[-1])
    # planted: accuracy 0.5 + 0.9/2 = 0.95 with the channel on vs 0.5 off, so the vector main effect is +0.45 (sampling sd ~0.05)
    assert eff["vector_memory"] > 0.30 and abs(eff["notes_table"]) < 0.20 and abs(eff["cache"]) < 0.20, eff
    # every flagged tile must sit in a vector-on row (the row label is the first child of the tile's row wrapper)
    labels = leak_tiles.evaluate_all("els => els.map(e => e.parentElement.firstElementChild.textContent)")
    assert labels and all("vector" in lb for lb in labels), labels
    # leaky reads all three channels: its single-channel main effects are small (redundancy)
    lg = page.locator("[data-testid=grid-trader-leaky]")
    leff = [float(re.findall(r"[+-]?\d+\.\d+", lg.locator(f"[data-effect='trader-leaky:{c}']").inner_text())[-1]) for c in ("vector_memory", "notes_table", "cache")]
    assert all(abs(x) < 0.25 for x in leff), leff


def test_commit_reveal_inspector_verifies_every_slot_in_the_browser_and_tamper_fails(live):
    page, _ = live
    goto(page, "/inspector")
    page.click("[data-testid=verify-all]")
    expect(page.locator("[data-testid=verify-all-result]")).to_contain_text("152 of 152 commitments verified", timeout=30000)
    expect(page.locator("[data-testid=verify-result]")).to_have_attribute("data-match", "1")
    h = page.locator("[data-testid=browser-hash]").inner_text()
    assert h == page.locator("[data-testid=db-commitment]").inner_text() and len(h) == 64
    for tamper in ("flip", "salt", "start"):
        page.click(f"[data-testid=tamper-{tamper}]")
        expect(page.locator("[data-testid=verify-result]")).to_have_attribute("data-match", "0")
        expect(page.locator("[data-testid=verify-result]")).to_contain_text("MISMATCH")
        assert page.locator("[data-testid=browser-hash]").inner_text() != h
    page.get_by_role("button", name="Reset").click()
    expect(page.locator("[data-testid=verify-result]")).to_have_attribute("data-match", "1")


def test_compliance_reports_for_the_live_campaign(live):
    page, _ = live
    goto(page, "/compliance")
    expect(page.locator("[data-testid=sdd-table] tbody tr").first).to_be_visible()
    page.click("[data-testid=tab-grants]")
    expect(page.locator("[data-testid=grant-review-ok]")).to_contain_text("Zero rows")
    page.click("[data-testid=tab-exposure]")
    expect(page.locator("[data-testid=exposure-table] tbody tr").first).to_be_visible(timeout=10000)
    expect(page.locator("[data-testid=exposure-table]")).to_contain_text("READ canary variant")
    expect(page.locator("[data-testid=exposure-table]")).to_contain_text("WRITE derived note")
    page.click("[data-testid=tab-lag]")
    expect(page.get_by_text("median (ms)").first).to_be_visible(timeout=10000)


def test_paraphrase_search_performance_and_peeking_pages(live):
    page, _ = live
    goto(page, "/compliance")
    page.click("[data-testid=tab-search]")
    page.click("[data-testid=search-run]")
    expect(page.locator("[data-testid=search-results] tbody tr").first).to_be_visible(timeout=15000)
    assert page.locator("[data-testid=search-results] tbody tr").count() == 10
    goto(page, "/performance")
    expect(page.locator("[data-testid=ab-table] tbody tr")).to_have_count(4)
    expect(page.locator("[data-testid=hnsw-table] tbody tr").first).to_be_visible()
    expect(page.locator("[data-testid=brin-table] tbody tr")).to_have_count(3)
    goto(page, "/power")
    expect(page.locator("[data-testid=peeking-summary]")).to_be_visible(timeout=20000)
    assert page.errors == []


def test_screenshots_every_page_both_viewports(live, browser, base_url):
    """Runs BEFORE the null-control test so the screenshots show the headline factorial campaign. Written to docs/screenshots/ (looked at, and fixed, during development). Also asserts no console errors and no horizontal overflow."""
    for vp, (w, h) in {"1440x900": (1440, 900), "1366x768": (1366, 768)}.items():
        for theme in ("light", "dark"):
            ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=theme)
            ctx.add_init_script(f"try{{localStorage.setItem('walltest-theme','{theme}')}}catch(e){{}}")
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            d = SHOTS / vp
            d.mkdir(parents=True, exist_ok=True)
            for name, path in PAGES:
                pg.goto(base_url + path)
                pg.wait_for_load_state("networkidle")
                pg.wait_for_timeout(1200)
                if name == "05-lab":
                    pg.click("[data-testid=lab-run-update_trade_order]")
                    pg.wait_for_selector("[data-testid=lab-result]")
                assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"horizontal overflow on {name} at {vp}"
                pg.screenshot(path=str(d / f"{name}-{theme}.png"))
            assert errs == [], (vp, theme, errs[:3])
            ctx.close()


def test_null_control_button_demonstrates_false_alarm_control(live):
    page, _ = live
    goto(page, "/")
    page.click("[data-testid=run-null]")
    expect(page.get_by_role("button", name="Stop run")).to_be_visible(timeout=15000)
    expect(page.get_by_role("button", name="Run live audit")).to_be_visible(timeout=120000)
    page.wait_for_timeout(2500)
    expect(page.locator("[data-testid=verdict-card-trader-clean]")).to_have_attribute("data-verdict", "NO_EVIDENCE", timeout=15000)
    assert page.locator("[data-testid^=verdict-card-]").count() == 1
    assert page.errors == []


def test_sequential_campaign_through_the_run_page(live):
    """The v2 controls: simulated clock, anytime-valid inference, stop at the first leak. The e-value chart is shown while it
    runs (watching is valid), and the frozen verdict says it stopped early."""
    page, _ = live
    goto(page, "/run")
    page.click("[data-testid=clock-SIMULATED]")
    page.select_option("[data-testid=design]", "FULL_FACTORIAL")
    page.fill("[data-testid=slots]", "400")
    page.click("[data-testid=inference-SEQUENTIAL]")
    page.select_option("[data-testid=stop-rule]", "FIRST_LEAK")
    page.click("[data-testid=start]")
    expect(page.locator("[data-testid=evalue-chart]")).to_be_visible(timeout=20000)
    expect(page.locator("[data-testid=verdict-ready]")).to_be_visible(timeout=120000)
    expect(page.locator("[data-testid=verdict-ready]")).to_contain_text("stopped early")
    expect(page.locator("[data-testid=verdict-ready]")).to_contain_text("signed")
    assert page.errors == []
