"""
modules/observability.py
========================
Phase 2 — Lightweight observability: in-memory metrics + dashboard.

Exposes:
  - update(key, value, delta=False)  — set or increment a metric
  - start_metrics_server(background=True) — Flask server on METRICS_SERVER_PORT
    GET /metrics   → JSON blob
    GET /dashboard → HTML dashboard
"""

import threading
import time
import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_metrics = {}
_lock = threading.Lock()


def update(key: str, value, delta: bool = False):
    """Set or increment a named metric."""
    with _lock:
        if delta:
            _metrics[key] = _metrics.get(key, 0) + value
        else:
            _metrics[key] = value
        _metrics["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"


def get_all() -> dict:
    """Return a snapshot of all metrics."""
    with _lock:
        return dict(_metrics)


def start_metrics_server(background: bool = True):
    """Start a Flask metrics/dashboard server."""
    try:
        from config.settings import METRICS_SERVER_PORT
    except ImportError:
        METRICS_SERVER_PORT = 8080

    try:
        from flask import Flask, jsonify, render_template_string
    except ImportError:
        print("[observability] ⚠️  Flask not installed — metrics server disabled.")
        return None

    app = Flask(__name__)

    DASHBOARD_HTML = """
    <!DOCTYPE html>
    <html>
    <head>
      <title>Cloud Cost Saver — Observability Dashboard</title>
      <meta http-equiv="refresh" content="10">
      <style>
        * { box-sizing:border-box; margin:0; padding:0; }
        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif;
               background:#0f172a; color:#e2e8f0; padding:2rem; }
        h1 { color:#38bdf8; margin-bottom:.5rem; }
        .sub { color:#64748b; margin-bottom:2rem; }
        .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:1rem; }
        .card { background:#1e293b; border-radius:12px; padding:1.5rem;
                border-left:4px solid #38bdf8; }
        .card h3 { font-size:.75rem; color:#94a3b8; text-transform:uppercase;
                    letter-spacing:.05em; margin-bottom:.5rem; }
        .card .value { font-size:1.8rem; font-weight:700; color:#f1f5f9; }
        .savings { border-color:#4ade80; }
        .savings .value { color:#4ade80; }
        .alert { border-color:#f59e0b; }
        .footer { margin-top:2rem; color:#475569; font-size:.8rem; }
      </style>
    </head>
    <body>
      <h1>📊 Observability Dashboard</h1>
      <p class="sub">Smart Cloud Cost Saver — Phase 2  |  Auto-refresh every 10s  |  {{ now }}</p>
      <div class="grid">
        {% for key, val in metrics.items() %}
        <div class="card {% if 'savings' in key %}savings{% elif 'alert' in key or 'blocked' in key %}alert{% endif %}">
          <h3>{{ key.replace('_', ' ') }}</h3>
          <div class="value">
            {% if val is number and val != val|int %}{{ "%.4f"|format(val) }}{% else %}{{ val }}{% endif %}
          </div>
        </div>
        {% endfor %}
      </div>
      <p class="footer">Pipeline metrics are in-memory and reset on restart.</p>
    </body>
    </html>
    """

    @app.route("/metrics")
    def metrics_json():
        return jsonify(get_all())

    @app.route("/dashboard")
    def dashboard():
        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        return render_template_string(DASHBOARD_HTML, metrics=get_all(), now=now)

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok", "service": "observability"})

    if background:
        t = threading.Thread(
            target=lambda: app.run(
                host="0.0.0.0", port=METRICS_SERVER_PORT,
                debug=False, use_reloader=False
            ),
            daemon=True,
            name="metrics-server",
        )
        t.start()
        time.sleep(0.3)
        return t
    else:
        app.run(host="0.0.0.0", port=METRICS_SERVER_PORT, debug=False)
