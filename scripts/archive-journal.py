#!/usr/bin/env python3
"""Split state/journal.md into per-month archives under state/archive/.

Run:  python3 scripts/archive-journal.py [--dry-run]

Why this exists: journal.md is append-only and the agent writes one entry per
run -- 52 runs a trading day when the file grew, 7 since the move to an hourly
cadence. It passed 1.15MB / 22k lines / 245 entries inside two weeks. Nothing
reads it whole -- run-context.sh tails 200 lines -- so size costs no tokens, but
an unbounded single file is a poor record and a slow one to work with by hand.

Entries are delimited by level-2 headings and there are two historical formats,
both carrying an ISO date:

    ## 2026-09-01 15:30 ET — no action, ...
    ## Run 43 — 2026-08-26, ~17:18 UTC (1:18 PM ET)

So months are keyed off the first YYYY-MM-DD found in the heading, never off
the heading's shape. A heading with no date at all is a malformed entry: it
stays with the preceding entry rather than being silently dropped.

WHAT STAYS LIVE. Months strictly older than the current ET month are archived,
oldest first -- but never so many that fewer than MIN_LIVE_ENTRIES remain in
journal.md. That guard is the whole reason this is not a one-line split: on the
1st of a month the current month holds a single entry, and archiving everything
else would leave run-context.sh tailing a file with no open positions and no
watch items in it. The agent would start the day blind. With the guard, the
newest archived month simply stays live one month longer and the split
self-corrects the following month.

Entries are never duplicated: an entry is either live or archived, never both.
"""
import os
import re
import shutil
import subprocess
import sys

JOURNAL = "state/journal.md"
ARCHIVE_DIR = "state/archive"
TITLE = "# Trading journal"

# Below this, a tail of 200 lines is not guaranteed to reach a complete entry.
# This was "one full trading day" when the cadence was every 30 minutes. The
# cadence is now hourly, so a trading day is 7 entries and 13 buys nearly two
# days instead of one. Deliberately not lowered to 7: the constant only sets
# how much history survives archiving, retaining more is strictly safer for
# run-context.sh, and the cost of holding an extra day is a few KB.
MIN_LIVE_ENTRIES = 13

HEADING = re.compile(r"^## ")
DATE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")


def et_now_month():
    """Current year-month in US Eastern, matching the agent's own clock.

    Shells out to date(1) rather than importing zoneinfo so this agrees exactly
    with run-agent.sh's session gate, which is the authoritative clock here.
    """
    out = subprocess.run(
        ["date", "+%Y-%m"], env={**os.environ, "TZ": "America/New_York"},
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def parse(lines):
    """-> (preamble_lines, [(month_or_None, heading_line, [body_lines])])."""
    preamble, entries = [], []
    for line in lines:
        if HEADING.match(line):
            m = DATE.search(line)
            entries.append(((f"{m.group(1)}-{m.group(2)}" if m else None), line, []))
        elif entries:
            entries[-1][2].append(line)
        else:
            preamble.append(line)
    return preamble, entries


def main():
    dry = "--dry-run" in sys.argv
    if not os.path.exists(JOURNAL):
        sys.exit(f"ERROR: {JOURNAL} not found -- run from the repo root.")

    with open(JOURNAL) as fh:
        lines = fh.readlines()
    preamble, entries = parse(lines)
    if not entries:
        sys.exit(f"ERROR: no '## ' entries found in {JOURNAL}; refusing to touch it.")

    undated = sum(1 for m, _, _ in entries if m is None)
    if undated:
        print(f"note: {undated} entry heading(s) carry no date; kept with the "
              f"preceding entry rather than archived.")

    current = et_now_month()
    months = sorted({m for m, _, _ in entries if m})
    print(f"{len(entries)} entries spanning {months[0]}..{months[-1]}; "
          f"current ET month is {current}")

    # Oldest first, stopping before the live file gets too thin to be useful.
    to_archive, live_count = [], len(entries)
    for month in months:
        if month >= current:
            break
        n = sum(1 for m, _, _ in entries if m == month)
        if live_count - n < MIN_LIVE_ENTRIES:
            print(f"keeping {month} live: archiving it would leave "
                  f"{live_count - n} entries, under the {MIN_LIVE_ENTRIES} "
                  f"needed for one trading day of context")
            break
        to_archive.append(month)
        live_count -= n

    if not to_archive:
        print("nothing to archive.")
        return

    # An entry belongs to an archived month only if it is dated. Undated
    # headings were folded into the preceding entry's body by parse(), so they
    # travel with it and cannot be lost here.
    for month in to_archive:
        body = [ln for m, h, b in entries if m == month for ln in ([h] + b)]
        path = f"{ARCHIVE_DIR}/journal-{month}.md"
        n = sum(1 for m, _, _ in entries if m == month)
        print(f"  -> {path}: {n} entries, {len(body)} lines")
        if not dry:
            os.makedirs(ARCHIVE_DIR, exist_ok=True)
            with open(path, "w") as fh:
                fh.write(f"{TITLE} — archive for {month}\n\n")
                fh.write("Archived from `state/journal.md` by "
                         "`scripts/archive-journal.py`. Historical record only; "
                         "the agent reads the live journal.\n\n")
                fh.writelines(body)

    kept = [(m, h, b) for m, h, b in entries if m not in to_archive]
    print(f"  -> {JOURNAL}: {len(kept)} entries retained")
    if dry:
        print("dry run; nothing written.")
        return

    # Backup before rewriting: state/ is gitignored, so there is no other copy.
    shutil.copy2(JOURNAL, JOURNAL + ".bak")
    with open(JOURNAL, "w") as fh:
        fh.writelines(preamble if preamble else [TITLE + "\n", "\n"])
        for _, h, b in kept:
            fh.write(h)
            fh.writelines(b)
    print(f"backup of the pre-split file at {JOURNAL}.bak")


if __name__ == "__main__":
    main()
