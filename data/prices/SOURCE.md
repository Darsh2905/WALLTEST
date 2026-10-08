# Price data provenance

**Real data. Not synthetic.**

| Item | Value |
|---|---|
| File | `nifty50_eod.csv` (4008 rows, 8 securities) |
| Fetched on | 2026-10-08 (UTC 07:14) |
| Date range | 2024-10-08 .. 2026-10-07 (NSE trading days) |
| Prices | Yahoo Finance chart API, NSE tickers `<SYMBOL>.NS`, interval 1d, unadjusted for dividends (split-adjusted by the source) |
| ISINs | NSE India: `https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv` (official NIFTY 50 constituents) cross-checked against `https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv` (NSE equity master) |
| ISIN checks | each ISIN is in both NSE files for that symbol, matches `^IN[A-Z0-9]{10}$`, and passes the ISO 6166 Luhn check digit |
| Rows dropped | bars with a missing O/H/L/C/volume; duplicate dates; high/low widened to contain open/close where the source rounded them inconsistently |

Securities: RELIANCE (INE002A01018), TCS (INE467B01029), HDFCBANK (INE040A01034), INFY (INE009A01021), ICICIBANK (INE090A01021), ITC (INE154A01025), SBIN (INE062A01020), LT (INE018A01030)

Re-create with `python scripts/fetch_prices.py`. Nothing in this directory is mock data.
