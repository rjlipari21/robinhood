"""REST API for the Robinhood AI dashboard."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import db

api = Blueprint("api", __name__, url_prefix="/api")


def _row_to_dict(row):
    return dict(row) if row is not None else None


@api.get("/account")
def get_account():
    conn = db.get_connection()
    account = conn.execute("SELECT * FROM accounts WHERE id = 1").fetchone()
    positions = conn.execute(
        "SELECT * FROM positions WHERE account_id = 1 ORDER BY market_value DESC"
    ).fetchall()
    equity_history = conn.execute(
        "SELECT date, equity FROM equity_history WHERE account_id = 1 ORDER BY date ASC"
    ).fetchall()
    conn.close()

    if account is None:
        return jsonify({"error": "no account data seeded yet"}), 404

    return jsonify({
        "account": _row_to_dict(account),
        "positions": [_row_to_dict(p) for p in positions],
        "equity_history": [_row_to_dict(e) for e in equity_history],
    })


@api.get("/decisions")
def list_decisions():
    symbol = request.args.get("symbol", "").strip().upper()
    action = request.args.get("action", "").strip().lower()
    start = request.args.get("start", "").strip()
    end = request.args.get("end", "").strip()
    limit = min(int(request.args.get("limit", 200)), 1000)
    offset = max(int(request.args.get("offset", 0)), 0)

    clauses = []
    params: list = []
    if symbol:
        clauses.append("d.symbol = ?")
        params.append(symbol)
    if action:
        clauses.append("d.action = ?")
        params.append(action)
    if start:
        clauses.append("r.run_at >= ?")
        params.append(start)
    if end:
        clauses.append("r.run_at <= ?")
        params.append(end)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    conn = db.get_connection()
    total = conn.execute(
        f"""SELECT COUNT(*) AS n FROM agent_decisions d
            JOIN agent_runs r ON r.id = d.run_id {where}""",
        params,
    ).fetchone()["n"]

    rows = conn.execute(
        f"""SELECT d.*, r.run_at, r.agent_name, r.trigger, r.status AS run_status,
                   r.summary AS run_summary, r.duration_ms
            FROM agent_decisions d
            JOIN agent_runs r ON r.id = d.run_id
            {where}
            ORDER BY r.run_at DESC, d.id DESC
            LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()
    conn.close()

    return jsonify({
        "total": total,
        "limit": limit,
        "offset": offset,
        "decisions": [_row_to_dict(r) for r in rows],
    })


@api.get("/runs/<int:run_id>")
def get_run(run_id: int):
    conn = db.get_connection()
    run = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    decisions = conn.execute(
        "SELECT * FROM agent_decisions WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    conn.close()

    if run is None:
        return jsonify({"error": "run not found"}), 404

    return jsonify({
        "run": _row_to_dict(run),
        "decisions": [_row_to_dict(d) for d in decisions],
    })


@api.get("/meta")
def get_meta():
    conn = db.get_connection()
    symbols = [r["symbol"] for r in conn.execute(
        "SELECT DISTINCT symbol FROM agent_decisions ORDER BY symbol"
    ).fetchall()]
    actions = [r["action"] for r in conn.execute(
        "SELECT DISTINCT action FROM agent_decisions ORDER BY action"
    ).fetchall()]
    conn.close()
    return jsonify({"symbols": symbols, "actions": actions})
