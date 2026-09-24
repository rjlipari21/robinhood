#!/usr/bin/env python3
"""Regression tests for news-brief.py's veto-keyword flagger.

Run:  python3 scripts/test-news-flags.py    (exit 0 = all pass)

Why this file exists: the flagger is the only part of the news screen that is
mechanical, and a broken regex fails SILENTLY -- it prints "FLAGS: none", which
reads exactly like "this stock is clean". The first version anchored patterns
with \\b at both ends, which meant "delist" could not match "delisting" and
"resign" could not match "resigns": a genuine delisting notice or CFO departure
produced no flag at all. That is the worst possible failure for a safety
screen, so it gets tests.

Add a case here whenever you touch VETO_PATTERNS. Cases are (headline,
expected_label_or_None) -- None means "must produce no flags", which guards
against over-matching just as much as the positives guard against misses.
"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location(
    "news_brief", __file__.replace("test-news-flags.py", "news-brief.py")
)
nb = importlib.util.module_from_spec(spec)
sys.modules["news_brief"] = nb
spec.loader.exec_module(nb)

# Positives: each must produce the named flag. Written with the real-world
# suffixes ("resigns", "delisting") that the both-ends anchoring used to miss.
POSITIVE = [
    ("Acme to be acquired by Globex in $4B deal", "MERGER"),
    ("Company explores going private at $22/share", "MERGER"),
    ("Board receives tender offer from strategic buyer", "MERGER"),
    ("Acquisitions drive expansion into new markets", "MERGER"),
    ("Biotech announces $200M public offering, shares slide", "DILUTION"),
    ("Company prices registered direct offering", "DILUTION"),
    ("Firm launches at-the-market equity program", "DILUTION"),
    ("Convertible note offering dilutes existing holders", "DILUTION"),
    ("SEC opens investigation into revenue recognition", "FRAUD"),
    ("Short-seller report alleges accounting irregularities", "FRAUD"),
    ("DOJ subpoena received over billing practices", "FRAUD"),
    ("Company will restate prior-year financials", "FRAUD"),
    ("XYZ Corp CFO resigns effective immediately", "MGMT_EXIT"),
    ("CEO steps down amid strategic review", "MGMT_EXIT"),
    ("Auditor departs ahead of annual filing", "MGMT_EXIT"),
    ("Nasdaq notifies company of delisting risk", "SOLVENCY"),
    ("Retailer files for Chapter 11 bankruptcy", "SOLVENCY"),
    ("Auditors flag going concern doubt", "SOLVENCY"),
    ("Board approves 1-for-10 reverse split", "SOLVENCY"),
    ("Phase 3 trial failed to meet primary endpoint", "TRIAL_FAIL"),
    ("Company discontinues lead candidate after data review", "TRIAL_FAIL"),
    ("FDA issues complete response letter", "TRIAL_FAIL"),
    ("Study missed its primary endpoint", "TRIAL_FAIL"),
    ("Retailer cuts guidance for full year", "GUIDANCE_CUT"),
    ("Manufacturer lowers outlook on weak demand", "GUIDANCE_CUT"),
    ("Company withdraws full-year guidance", "GUIDANCE_CUT"),
]

# Negatives: ordinary market noise that must NOT flag. These are the ones that
# matter for cost and trust -- a flagger that fires on every earnings story
# trains the agent to ignore it, which is the same as having no screen.
NEGATIVE = [
    "Quarterly revenue rises 12% on strong demand",
    "Analyst raises price target to $95",
    "Stock climbs after earnings beat",
    "Dividend increased for the 60th consecutive year",
    "Shares fall as broader market retreats on rate fears",
    "Company opens new distribution centre in Texas",
    "CEO discusses growth strategy at investor conference",
    "Insider sold shares worth $10,126,453, per SEC filing",
    "QUICK SPARK: Nvidia Stock Adds $370 Billion Market Value",
    "Warren Buffett Set Berkshire Up for Years: Top 5 Stocks",
    "Jim Cramer says the stock is a winner",
    "Accounting software firm reports record bookings",
]


def main() -> int:
    failures = []

    for text, want in POSITIVE:
        got = nb.flags_for(text)
        if want not in got:
            failures.append(f"MISS  expected {want:12} got {got!s:22} <- {text}")

    for text in NEGATIVE:
        got = nb.flags_for(text)
        if got:
            failures.append(f"FALSE got {got!s:22} expected none          <- {text}")

    total = len(POSITIVE) + len(NEGATIVE)
    for line in failures:
        print(line)
    print(f"\n{total - len(failures)}/{total} passed "
          f"({len(POSITIVE)} positive, {len(NEGATIVE)} negative)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
