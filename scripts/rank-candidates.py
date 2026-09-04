#!/usr/bin/env python3
"""Union, screen and rank the two saved scans into the top-50 candidate list.

Implements step 7a-7b of prompts/trading-run.md deterministically, in one
call, so the agent does not spend a dozen turns per run re-deriving the same
jq/awk pipeline. Before this existed, the ranking step cost 15-25 Bash turns
of a 45-turn budget -- four runs on 2026-09-01 hit the ceiling, and one of
them died after review_equity_order but before place_equity_order.

Input is NOT stdin: run_scan returns ~85KB per scan, far too large to pipe
through a shell argument. Claude Code spills oversized MCP results to
  <project-transcripts>/<session-id>/tool-results/mcp-*-run_scan-<ms>.txt
and this reads the two most recent of those directly. The agent therefore
just calls run_scan twice and then runs this -- no copying payloads around.

That spill path is a Claude Code implementation detail, so treat it as load-
bearing: if an upgrade changes it, this exits 3 with a clear message rather
than silently ranking nothing, and the agent falls back to reading the scan
output itself.

Ranking is relative volume descending, tie-broken by % Change descending --
exactly the mandate's ordering. Only the $5 floor is enforced as a hard drop.
See FUND_HINTS for why the "common stocks only" rule is flagged, not applied.
"""
import glob
import json
import os
import re
import sys
import time

MAX_CANDIDATES = 50
MIN_PRICE = 5.0
# A run_scan spill older than this is from an earlier run, not this one.
# The scheduler fires every 15 min, so anything past ~10 min means the
# run_scan calls for THIS run did not land where we expect.
STALE_AFTER_SEC = 600

# CLAUDE.md requires common stocks only -- no ETFs, ETPs, closed-end funds or
# trusts. The scanner cannot answer that question: every row comes back with
# "Asset type": "STOCK", including outright closed-end funds (Nuveen AMT-Free
# Quality Municipal Income Fund ranked 2nd on 2026-09-01 with that label). So
# the only signal available here is the name, and a name regex is too blunt to
# drop on: REITs like "Pebblebrook Hotel Trust" are common stocks and perfectly
# buyable, but would match any pattern broad enough to catch the Nuveen funds.
#
# Hence flag, never drop. A FUND? row still reaches the agent, which applies
# the mandate's "if you are unsure whether a ticker is a fund, skip it".
# Silently dropping would hide buyable REITs; not flagging at all would let a
# municipal bond fund top the list unremarked, which is what happened.
FUND_HINTS = re.compile(
    r"\b(fund|etf|etn|municipal|index|portfolio|closed[- ]end|"
    r"income trust|royalty trust|unit trust)\b", re.I)

PROJECT_SLUG = "-home-rjlipari21-robinhood"
TRANSCRIPTS = os.path.expanduser(f"~/.claude/projects/{PROJECT_SLUG}")


def find_scan_spills():
    """The two most recent run_scan result spills, newest first."""
    pattern = os.path.join(TRANSCRIPTS, "*", "tool-results",
                           "mcp-robinhood-trading-run_scan-*.txt")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        sys.exit(f"ERROR(3): no run_scan spill files under {TRANSCRIPTS}/*/tool-results/.\n"
                 "Call run_scan on both scan ids first. If you just did, Claude Code may have\n"
                 "changed where it spills large tool results -- rank the scans by hand this run\n"
                 "and report the path change in the journal.")
    age = time.time() - os.path.getmtime(files[0])
    if age > STALE_AFTER_SEC:
        sys.exit(f"ERROR(3): newest run_scan spill is {age/60:.1f} min old -- that is a previous\n"
                 "run's data, not this one's. Call run_scan on both scan ids before ranking.")
    return files[:2]


def load_rows(paths):
    """Flatten scan payloads into rows, noting server-side truncation."""
    rows, scans = [], []
    for path in paths:
        with open(path) as fh:
            result = json.load(fh)["data"]["result"]
        got = result.get("results", [])
        scans.append({
            "id": result.get("scan_id", "?"),
            "returned": len(got),
            "total": result.get("total_items", len(got)),
        })
        rows.extend(got)
    return rows, scans


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def screen_and_rank(rows):
    """Dedupe by ticker, drop sub-$5, rank by relative volume."""
    by_ticker, dropped = {}, {"dup": 0, "cheap": 0, "bad_data": 0}

    for row in rows:
        ticker = row.get("ticker")
        cols = row.get("columns", {})
        if not ticker:
            dropped["bad_data"] += 1
            continue
        if ticker in by_ticker:
            dropped["dup"] += 1
            continue

        last = to_float(cols.get("Last"))
        if last is None:
            dropped["bad_data"] += 1
            continue
        if last < MIN_PRICE:
            dropped["cheap"] += 1
            continue

        # Only one of the two scans exposes a "Relative volume" column, so
        # fall back to computing it. Both agree to 4dp where both are present.
        relvol = to_float(cols.get("Relative volume"))
        if relvol is None:
            volume = to_float(cols.get("Volume"))
            avg_volume = to_float(cols.get("Average volume"))
            if volume is None or not avg_volume:
                dropped["bad_data"] += 1
                continue
            relvol = volume / avg_volume

        name = cols.get("Name") or ""
        by_ticker[ticker] = {
            "ticker": ticker,
            "last": last,
            # Scans report % Change as a fraction (-0.0167 = -1.67%).
            "pct": (to_float(cols.get("% Change")) or 0.0) * 100.0,
            "relvol": relvol,
            "rsi": to_float(cols.get("RSI (14, 1H)")),
            "flag": "FUND?" if FUND_HINTS.search(name) else "",
            "name": name[:34],
        }

    ranked = sorted(by_ticker.values(),
                    key=lambda r: (r["relvol"], r["pct"]), reverse=True)
    return ranked, dropped


def main():
    paths = find_scan_spills()
    rows, scans = load_rows(paths)
    ranked, dropped = screen_and_rank(rows)
    kept = ranked[:MAX_CANDIDATES]

    for s in scans:
        note = "" if s["returned"] >= s["total"] else \
            f"  <-- TRUNCATED by the scanner, {s['total'] - s['returned']} rows never returned"
        print(f"# scan {s['id']}: {s['returned']}/{s['total']} rows{note}")
    print(f"# union {len(rows)} rows -> {len(ranked)} eligible -> top {len(kept)} shown")
    print(f"# dropped: {dropped['dup']} dup, {dropped['cheap']} under "
          f"${MIN_PRICE:.0f}, {dropped['bad_data']} bad-data")
    if len(ranked) > len(kept):
        print(f"# NOTE: {len(ranked) - len(kept)} eligible names cut by the top-{MAX_CANDIDATES} cap")
    print("# FUND? = name looks like a fund/ETF/CEF; verify and skip if unsure. REITs are fine.")
    print("rank\tticker\tlast\tpct_chg\trelvol\trsi_1h\tflag\tname")
    for i, r in enumerate(kept, 1):
        rsi = f"{r['rsi']:.1f}" if r["rsi"] is not None else "-"
        print(f"{i}\t{r['ticker']}\t{r['last']:.2f}\t{r['pct']:+.2f}\t"
              f"{r['relvol']:.2f}\t{rsi}\t{r['flag']}\t{r['name']}")


if __name__ == "__main__":
    main()
