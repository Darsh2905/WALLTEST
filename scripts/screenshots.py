#!/usr/bin/env python3
"""Screenshot every page at 1440x900 and 1366x768 into docs/screenshots/<viewport>/. Usage: screenshots.py [--base URL] [--theme light|dark] [--out DIR]"""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PAGES = [("01-wall", "/"), ("02-run", "/run"), ("03-verdicts", "/verdicts"), ("04-inspector", "/inspector"), ("05-lab", "/lab"),
         ("06-compliance", "/compliance"), ("07-power", "/power"), ("08-schema", "/schema")]
VIEWPORTS = {"1440x900": (1440, 900), "1366x768": (1366, 768)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--theme", default="light")
    ap.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    ap.add_argument("--full", action="store_true", help="full-page screenshots")
    a = ap.parse_args()
    with sync_playwright() as p:
        b = p.chromium.launch()
        for vp, (w, h) in VIEWPORTS.items():
            ctx = b.new_context(viewport={"width": w, "height": h}, color_scheme=a.theme)
            ctx.add_init_script(f"try{{localStorage.setItem('walltest-theme','{a.theme}')}}catch(e){{}}")
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            d = Path(a.out) / vp
            d.mkdir(parents=True, exist_ok=True)
            for name, path in PAGES:
                page.goto(a.base + path)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(900)
                if name == "05-lab":
                    page.click("[data-testid=lab-run-update_trade_order]")
                    page.wait_for_selector("[data-testid=lab-result]")
                if name == "06-compliance":
                    pass
                page.screenshot(path=str(d / f"{name}-{a.theme}.png"), full_page=a.full)
            print(vp, "console/page errors:", errors[:5])
            ctx.close()
        b.close()


if __name__ == "__main__":
    main()
