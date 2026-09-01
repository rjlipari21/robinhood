"""Entrypoint for the Robinhood AI Account Dashboard.

Run with:  python3 app.py
Then open: http://localhost:5000
"""

from __future__ import annotations

from flask import Flask, render_template

from robinhood_dashboard import db, seed
from robinhood_dashboard.api import api


def create_app() -> Flask:
    app = Flask(__name__)

    conn = db.init_db()
    if db.is_empty(conn):
        seed.seed(conn)
    conn.close()

    app.register_blueprint(api)

    @app.get("/")
    def index():
        return render_template("index.html")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
