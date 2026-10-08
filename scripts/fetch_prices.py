#!/usr/bin/env python3
"""Fetch real NIFTY 50 end-of-day prices and write data/prices/nifty50_eod.csv.

Sources
  * Prices : Yahoo Finance chart API, NSE tickers (<SYMBOL>.NS), daily bars.
  * ISINs  : NSE's own files, never guessed or taken from Yahoo:
             - https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv
               (official NIFTY 50 constituent list, has "ISIN Code")
             - https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv
               (NSE equity master, has "ISIN NUMBER") -- used as a second, independent check.
  Every ISIN must (a) appear in both NSE files for that symbol, (b) match ^IN[A-Z0-9]{10}$ and
  (c) pass the ISO 6166 Luhn check digit.  Any failure aborts: no row is written with an unverified ISIN.

Usage:  python scripts/fetch_prices.py [--symbols A,B,...] [--range 2y]
Writes: data/prices/nifty50_eod.csv and data/prices/SOURCE.md
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "prices"
UA = {"User-Agent": "Mozilla/5.0 (WALLTEST DBMS lab; educational)"}
NIFTY50_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"
EQUITY_L_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}.NS"

# 8 large caps across sectors (all constituents of NIFTY 50; membership is re-verified below)
DEFAULT_SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "ITC", "SBIN", "LT"]


def get(url: str, **kw) -> requests.Response:
    last = None
    for attempt in range(4):
        try:
            r = requests.get(url, headers=UA, timeout=40, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def luhn_isin_ok(isin: str) -> bool:
    """ISO 6166 check digit: letters -> two digits (A=10..Z=35), then Luhn over the digit string."""
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
        return False
    digits = "".join(str(int(c, 36)) for c in isin)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return total % 10 == 0


def nse_isins(symbols: list[str]) -> dict[str, dict]:
    n50 = list(csv.DictReader(io.StringIO(get(NIFTY50_URL).text)))
    eq = list(csv.DictReader(io.StringIO(get(EQUITY_L_URL).text)))
    eq_isin = {r["SYMBOL"].strip(): r[" ISIN NUMBER"].strip() for r in eq if r.get(" ISIN NUMBER")}
    by_sym = {r["Symbol"].strip(): r for r in n50}
    out: dict[str, dict] = {}
    for s in symbols:
        if s not in by_sym:
            raise SystemExit(f"{s} is not in the NSE NIFTY 50 constituent list")
        isin = by_sym[s]["ISIN Code"].strip()
        if eq_isin.get(s) != isin:
            raise SystemExit(f"ISIN mismatch for {s}: nifty50 list {isin} vs EQUITY_L {eq_isin.get(s)}")
        if not re.fullmatch(r"IN[A-Z0-9]{10}", isin) or not luhn_isin_ok(isin):
            raise SystemExit(f"ISIN {isin} for {s} fails format / ISO 6166 check digit")
        out[s] = {"isin": isin, "company": by_sym[s]["Company Name"].strip(), "sector": by_sym[s]["Industry"].strip()}
    return out


def yahoo_bars(sym: str, rng: str) -> list[dict]:
    j = get(YAHOO_URL.format(sym=sym), params={"range": rng, "interval": "1d", "includeAdjustedClose": "false"}).json()
    res = j["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    bars = []
    for i, t in enumerate(ts):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c, v):
            continue
        d = dt.datetime.fromtimestamp(t, dt.timezone(dt.timedelta(hours=5, minutes=30))).date()
        # keep the row internally consistent (the DB CHECK is low <= open,close <= high)
        h = max(h, o, c)
        l = min(l, o, c)
        bars.append({"trade_date": d.isoformat(), "open": round(o, 2), "high": round(h, 2),
                     "low": round(l, 2), "close": round(c, 2), "volume": int(v)})
    # drop today's bar if the NSE session has not closed yet (it would be an incomplete intraday bar)
    ist = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    if ist.hour < 16 and bars and bars[-1]["trade_date"] == ist.date().isoformat():
        bars = bars[:-1]
    # de-duplicate dates (Yahoo occasionally repeats the latest day)
    seen, uniq = set(), []
    for b in bars:
        if b["trade_date"] not in seen:
            seen.add(b["trade_date"])
            uniq.append(b)
    return uniq


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--range", default="2y")
    a = ap.parse_args()
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    meta = nse_isins(symbols)
    rows = []
    for s in symbols:
        bars = yahoo_bars(s, a.range)
        if len(bars) < 250:
            raise SystemExit(f"{s}: only {len(bars)} bars, need >= 250 (about one trading year)")
        for b in bars:
            rows.append({"isin": meta[s]["isin"], "symbol": s, "company_name": meta[s]["company"],
                         "sector": meta[s]["sector"], **b})
        print(f"{s:10s} {meta[s]['isin']}  {len(bars)} bars  {bars[0]['trade_date']} .. {bars[-1]['trade_date']}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cols = ["isin", "symbol", "company_name", "sector", "trade_date", "open", "high", "low", "close", "volume"]
    with open(OUT_DIR / "nifty50_eod.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["symbol"], r["trade_date"])))
    first = min(r["trade_date"] for r in rows)
    last = max(r["trade_date"] for r in rows)
    (OUT_DIR / "SOURCE.md").write_text(f"""# Price data provenance

**Real data. Not synthetic.**

| Item | Value |
|---|---|
| File | `nifty50_eod.csv` ({len(rows)} rows, {len(symbols)} securities) |
| Fetched on | {dt.date.today().isoformat()} (UTC {dt.datetime.now(dt.timezone.utc):%H:%M}) |
| Date range | {first} .. {last} (NSE trading days) |
| Prices | Yahoo Finance chart API, NSE tickers `<SYMBOL>.NS`, interval 1d, unadjusted for dividends (split-adjusted by the source) |
| ISINs | NSE India: `{NIFTY50_URL}` (official NIFTY 50 constituents) cross-checked against `{EQUITY_L_URL}` (NSE equity master) |
| ISIN checks | each ISIN is in both NSE files for that symbol, matches `^IN[A-Z0-9]{{10}}$`, and passes the ISO 6166 Luhn check digit |
| Rows dropped | bars with a missing O/H/L/C/volume; duplicate dates; high/low widened to contain open/close where the source rounded them inconsistently |

Securities: {", ".join(f"{s} ({meta[s]['isin']})" for s in symbols)}

Re-create with `python scripts/fetch_prices.py`. Nothing in this directory is mock data.
""")
    print(f"wrote {len(rows)} rows to {OUT_DIR/'nifty50_eod.csv'}")


if __name__ == "__main__":
    sys.exit(main())
