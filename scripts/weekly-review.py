#!/usr/bin/env python3
"""Weekly review: how each rule is actually doing, from state/trades.jsonl.

The 2026-10-01 performance review was done by hand and found the thing that
mattered -- wins averaging +$2 against losses averaging -$6 -- only because
someone asked. This makes that review routine. Deterministic arithmetic, no
model: the numbers are the point, and they must be trustworthy and free.

Runs Fridays after the close (rh-weekly-review@.timer, installed by
scripts/install-weekly-review.sh) and can be run by hand any time. It:

  * splits closed trades by entry path, exit reason, analyst consensus and
    upside at entry, SPY's position at entry, and position size;
  * reports each split over three windows -- the last 7 days, since the
    2026-10-01 rule changes, and all time -- because the rules changed a lot
    that day and mixing eras hides whether the new rules work;
  * flags only segments with enough trades to mean something (MIN_N), and
    says "too few to judge" otherwise -- with ~30 trades a month, a single
    trade like RVMD (-$35) swings a segment, and acting on that is chasing
    noise;
  * writes state/reviews/review-YYYY-MM-DD.md (full) and state/scorecard.md
    (short, printed to the agent by scripts/run-context.sh every run as
    background -- it never overrides the rules), and pushes a summary to the
    owner's phone.

It proposes nothing and changes nothing. Rule changes stay with the owner.

Usage: python3 scripts/weekly-review.py [--no-notify]
"""
import datetime as dt
import json
import os
import subprocess
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES = os.path.join(ROOT, "state", "trades.jsonl")
FILLS = os.path.join(ROOT, "state", "fills.jsonl")
REVIEWS = os.path.join(ROOT, "state", "reviews")
SCORECARD = os.path.join(ROOT, "state", "scorecard.md")
RULES_SINCE = "2026-10-01"     # the day the current rule set took effect
MIN_N = 10                     # below this a segment is "too few to judge"


def parse_ts(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_trades():
    try:
        with open(TRADES) as fh:
            return [json.loads(l) for l in fh if l.strip()]
    except FileNotFoundError:
        sys.exit("weekly-review: no state/trades.jsonl -- run scripts/trade-record.py first")


def ctx(t, *path):
    c = t.get("context")
    for p in path:
        if not isinstance(c, dict):
            return None
        c = c.get(p)
    return c


# ---- segment keys --------------------------------------------------------

def seg_path(t):
    return t.get("entry_path") or "unknown"


def seg_exit(t):
    r = t.get("exit_reason") or "unknown"
    return r + (" (inferred)" if t.get("exit_reason_source") == "inferred" else "")


def seg_analyst(t):
    a = ctx(t, "analyst")
    if a is None:
        return "not recorded"
    if a == "no coverage" or not isinstance(a, dict) or a.get("buy_pct") is None:
        return "no coverage"
    p = a["buy_pct"]
    return "favoured (>=70% buy)" if p >= 70 else "mixed (40-70% buy)" if p >= 40 else "unloved (<40% buy)"


def seg_upside(t):
    a = ctx(t, "analyst")
    u = a.get("upside_pct") if isinstance(a, dict) else None
    if u is None:
        return "not recorded"
    return "target >=15% above" if u >= 15 else "target 0-15% above" if u >= 0 else "target below price"


def seg_spy(t):
    v = ctx(t, "market", "vs_sma_pct")
    if v is None:
        return "not recorded"
    return "SPY above 20d avg" if v >= 0 else "SPY below 20d avg"


def seg_size(t):
    n = t.get("notional")
    if n is None:
        return "unknown"
    return "<= $150" if n <= 150 else "> $150 (pre-cap)"


SEGMENTS = [
    ("Entry path", seg_path),
    ("Exit reason", seg_exit),
    ("Analyst consensus at entry", seg_analyst),
    ("Analyst target vs entry price", seg_upside),
    ("Market at entry", seg_spy),
    ("Position size", seg_size),
]


# ---- stats ---------------------------------------------------------------

def stats(trades):
    pnl = [t["pnl_usd"] for t in trades if t.get("pnl_usd") is not None]
    pct = [t["pnl_pct"] for t in trades if t.get("pnl_pct") is not None]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p <= 0]
    n = len(pnl)
    return {
        "n": n,
        "win_pct": 100 * len(wins) / n if n else None,
        "avg_win": sum(wins) / len(wins) if wins else None,
        "avg_loss": sum(losses) / len(losses) if losses else None,
        "total": sum(pnl),
        "per_trade": sum(pnl) / n if n else None,
        "avg_pct": sum(pct) / len(pct) if pct else None,
    }


def money(v):
    return "-" if v is None else f"{'+' if v >= 0 else '-'}${abs(v):.2f}"


def row(label, s):
    judge = "" if s["n"] >= MIN_N else "  (too few to judge)"
    wp = "-" if s["win_pct"] is None else f"{s['win_pct']:.0f}%"
    ap = "-" if s["avg_pct"] is None else f"{s['avg_pct']:+.2f}%"
    return (f"| {label} | {s['n']} | {wp} | {money(s['avg_win'])} | {money(s['avg_loss'])} "
            f"| {money(s['per_trade'])} | {ap} | {money(s['total'])}{judge} |")


HEADER = ("| | Trades | Win rate | Avg win | Avg loss | Per trade | Avg % | Total |\n"
          "|---|---|---|---|---|---|---|---|")


UNLABELLED = {"unknown", "not recorded", "pre-agent"}


def flags(trades, label):
    """Plain statements about segments big enough to judge. Never advice."""
    out = []
    s = stats(trades)
    if s["n"] >= MIN_N and s["avg_win"] and s["avg_loss"]:
        ratio = abs(s["avg_loss"]) / s["avg_win"]
        if ratio >= 1.5:
            need = 100 * ratio / (1 + ratio)
            out.append(f"{label}: average loss is {ratio:.1f}x the average win, so it needs a "
                       f"{need:.0f}% win rate to break even; it has {s['win_pct']:.0f}%.")
    for name, key in SEGMENTS:
        groups = defaultdict(list)
        for t in trades:
            groups[key(t)].append(t)
        for g, ts in groups.items():
            # A segment with no label, or one holding every trade, just
            # restates the overall line -- not a finding about a rule.
            if g in UNLABELLED or len(ts) == len(trades):
                continue
            gs = stats(ts)
            if gs["n"] >= MIN_N and gs["per_trade"] is not None and gs["per_trade"] < 0:
                out.append(f"{label}: {name.lower()} '{g}' is losing {money(gs['per_trade'])} "
                           f"per trade over {gs['n']} trades.")
    return out


def section(title, trades):
    lines = [f"## {title}", ""]
    if not trades:
        return lines + ["No closed trades in this window.", ""]
    lines += [HEADER, row("**All trades**", stats(trades)), ""]
    for name, key in SEGMENTS:
        groups = defaultdict(list)
        for t in trades:
            groups[key(t)].append(t)
        if len(groups) == 1 and "not recorded" in groups:
            continue
        lines += [f"**{name}**", "", HEADER]
        for g in sorted(groups, key=lambda g: -len(groups[g])):
            lines.append(row(g, stats(groups[g])))
        lines.append("")
    return lines


def data_quality(trades, since):
    recent = [t for t in trades if str(t.get("opened_at") or "") >= since]
    no_path = sum(1 for t in recent if t.get("entry_path") in (None, "unknown"))
    no_ctx = sum(1 for t in recent if not isinstance(t.get("context"), dict)
                 or not t["context"].get("captured_at"))
    inferred = sum(1 for t in trades if t["status"] == "closed"
                   and str(t.get("closed_at") or "") >= since
                   and t.get("exit_reason_source") == "inferred")
    missed = 0
    try:
        with open(FILLS) as fh:
            for l in fh:
                try:
                    d = json.loads(l)
                except json.JSONDecodeError:
                    continue
                if str(d.get("filled_at") or "") >= since and \
                        "reconcile-fills.py" in str(d.get("note") or ""):
                    missed += 1
    except FileNotFoundError:
        pass
    return [
        f"- Entries since {since} with no entry path recorded: {no_path} of {len(recent)}",
        f"- Entries since {since} with no market/analyst context: {no_ctx} of {len(recent)}",
        f"- Exits since {since} with the reason inferred, not recorded: {inferred}",
        f"- Fills since {since} the agent missed (added by reconcile-fills.py): {missed}",
    ]


def notify(title, body):
    try:
        subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "notify.py"),
                        "summary", title, body], timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def main():
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    week_ago = (now - dt.timedelta(days=7)).isoformat()
    trades = load_trades()
    closed = [t for t in trades if t["status"] == "closed"]
    open_ = [t for t in trades if t["status"] == "open"]
    week = [t for t in closed if str(t.get("closed_at") or "") >= week_ago]
    current = [t for t in closed if str(t.get("opened_at") or "") >= RULES_SINCE]

    fl = flags(current, "Current rules") or []
    fl += flags(closed, "All time")

    full = [f"# Weekly review — {today}", "",
            f"Generated by `scripts/weekly-review.py` from `state/trades.jsonl`. "
            f"Segments with fewer than {MIN_N} trades are marked too few to judge. "
            f"This report proposes nothing; rule changes are the owner's call.", ""]
    full += ["## Flags", ""] + ([f"- {x}" for x in fl] or ["- None: no segment has enough trades to flag."]) + [""]
    full += section("Last 7 days", week)
    full += section(f"Current rules (entries since {RULES_SINCE})", current)
    full += section("All time", closed)
    full += ["## Open positions", ""]
    full += [f"- {t['symbol']}: {t['qty']:g} @ {t['entry_price']:.2f}, opened {str(t['opened_at'])[:10]}, "
             f"entry {t.get('entry_path')}" for t in open_ if t.get("entry_price")] or ["- None"]
    full += ["", "## Data quality", ""] + data_quality(trades, RULES_SINCE) + [""]

    os.makedirs(REVIEWS, exist_ok=True)
    path = os.path.join(REVIEWS, f"review-{today}.md")
    with open(path, "w") as fh:
        fh.write("\n".join(full))
    # Machine-readable twin, for scripts/propose-rules.sh: it drafts rule-change
    # proposals only when a review actually flags something.
    with open(os.path.join(REVIEWS, f"review-{today}.json"), "w") as fh:
        json.dump({"date": today, "flags": fl, "min_n": MIN_N,
                   "current_rules": stats(current), "all_time": stats(closed),
                   "report": f"state/reviews/review-{today}.md"}, fh, indent=1)

    cs, al = stats(current), stats(closed)
    card = [f"# Scorecard ({today}, from the weekly review)", "",
            "Background only — what the record shows so far. It never overrides the rules.", "",
            HEADER,
            row(f"Current rules (since {RULES_SINCE})", cs),
            row("All time", al), ""]
    for name, key in SEGMENTS[:2]:
        groups = defaultdict(list)
        for t in current:
            groups[key(t)].append(t)
        if groups:
            card += [f"{name}, current rules:", HEADER]
            card += [row(g, stats(ts)) for g, ts in sorted(groups.items(), key=lambda x: -len(x[1]))]
            card.append("")
    card += ["Flags:"] + ([f"- {x}" for x in fl] or ["- None yet (too few trades)."])
    card += ["", f"Full report: state/reviews/review-{today}.md"]
    tmp = SCORECARD + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("\n".join(card) + "\n")
    os.replace(tmp, SCORECARD)

    ws = stats(week)
    body = (f"This week: {ws['n']} closed, {money(ws['total'])}"
            + (f", {ws['win_pct']:.0f}% wins" if ws["n"] else "") + "\n"
            f"Since {RULES_SINCE}: {cs['n']} closed, {money(cs['total'])}\n"
            f"All time: {al['n']} closed, {money(al['total'])}\n"
            f"{len(open_)} open. " + (f"{len(fl)} flag(s): {fl[0]}" if fl else "No flags yet.")
            + f"\nReport: state/reviews/review-{today}.md")
    print(body)
    if "--no-notify" not in sys.argv:
        notify("Weekly trading review", body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
