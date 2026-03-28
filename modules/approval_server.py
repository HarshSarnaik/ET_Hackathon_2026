"""
approval_server.py
==================
Phase 1 Manual Approval Server (Flask).

Handles one-click actions from Twilio alert links:
  GET /approve?id=<resource_id>   → mark approved, trigger executor
  GET /reject?id=<resource_id>    → mark rejected, log
  GET /snooze?id=<resource_id>&hours=24  → suppress alerts for N hours
  GET /exempt?id=<resource_id>&days=7   → exempt VM from detection for N days
  GET /status                     → dashboard of all pending approvals
  GET /healthz                    → liveness probe

Approval state is persisted to data/pending_approvals.json.
The escalation watchdog (run in a background thread) re-alerts if no ACK
within ESCALATION_TIMEOUT_MINUTES.

Usage:
    python modules/approval_server.py          # standalone
    # or import start_approval_server() from main.py
"""

import json
import os
import sys
import datetime
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    APPROVAL_HOST,
    APPROVAL_PORT,
    APPROVAL_DB_PATH,
    ESCALATION_TIMEOUT_MINUTES,
    USE_MOCK,
)


def _record_outcome(entry: dict, outcome: str, remote_addr: str | None):
    if not entry:
        return
    vm = entry.get("vm_snapshot") or {}
    from modules.feedback import record_feedback

    record_feedback(
        resource_id=entry.get("resource_id", ""),
        name=entry.get("name", ""),
        outcome=outcome,
        owner_team=entry.get("owner_team", "default"),
        idle_score=float(vm.get("idle_score") or 0),
        decision_confidence=float(vm.get("decision_confidence") or 0),
        explanation=vm.get("idle_analysis", {}).get("explanation", []),
        remote_addr=remote_addr,
    )


# ── Approval DB helpers ───────────────────────────────────────────────────────

def _load_db() -> dict:
    os.makedirs(os.path.dirname(APPROVAL_DB_PATH), exist_ok=True)
    try:
        with open(APPROVAL_DB_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_db(db: dict):
    os.makedirs(os.path.dirname(APPROVAL_DB_PATH), exist_ok=True)
    with open(APPROVAL_DB_PATH, "w") as f:
        json.dump(db, f, indent=2, default=str)


def register_pending(vm: dict):
    """Register a VM as pending approval (called from main pipeline)."""
    db  = _load_db()
    rid = vm["resource_id"]
    db[rid] = {
        "resource_id"        : rid,
        "name"               : vm["name"],
        "instance_type"      : vm.get("instance_type"),
        "environment"        : vm.get("environment"),
        "owner_team"         : vm.get("owner_team"),
        "severity"           : vm.get("cost_analysis", {}).get("severity", "LOW"),
        "waste_so_far_inr"   : vm.get("cost_analysis", {}).get("waste_so_far_inr", 0),
        "predicted_savings_30d_usd": vm.get("predicted_savings_30d_usd", 0),
        "decision_confidence": vm.get("decision_confidence", 0),
        "explanation"        : vm.get("idle_analysis", {}).get("explanation", []),
        "status"             : "PENDING",
        "alerted_at"         : datetime.datetime.utcnow().isoformat() + "Z",
        "resolved_at"        : None,
        "resolved_by"        : None,
        "escalated"          : False,
        "snooze_until"       : None,
        "exempt_until"       : None,
        "vm_snapshot"        : vm,
    }
    _save_db(db)


def get_pending() -> list:
    """Return all VMs currently awaiting approval."""
    db = _load_db()
    return [v for v in db.values() if v["status"] == "PENDING"]


def is_exempt(resource_id: str) -> bool:
    """Returns True if VM is currently snoozed or exempted."""
    db    = _load_db()
    entry = db.get(resource_id)
    if not entry:
        return False
    now = datetime.datetime.utcnow().isoformat() + "Z"
    if entry.get("snooze_until") and entry["snooze_until"] > now:
        return True
    if entry.get("exempt_until") and entry["exempt_until"] > now:
        return True
    return False


# ── Flask App ─────────────────────────────────────────────────────────────────

def create_app():
    try:
        from flask import Flask, request, jsonify, render_template_string
    except ImportError:
        print("[approval_server] ❌  Flask not installed. Run: pip install flask")
        return None

    app = Flask(__name__)

    STATUS_PAGE = """
    <!DOCTYPE html>
    <html>
    <head>
      <title>Cloud Cost Saver — Approval Dashboard</title>
      <style>
        body { font-family: -apple-system, sans-serif; background:#0f172a; color:#e2e8f0; padding:2rem; }
        h1   { color:#38bdf8; }
        .card{ background:#1e293b; border-radius:8px; padding:1.5rem; margin:1rem 0; border-left:4px solid #38bdf8; }
        .CRITICAL { border-color:#ef4444; }
        .HIGH     { border-color:#f97316; }
        .MEDIUM   { border-color:#facc15; }
        .LOW      { border-color:#4ade80; }
        .btn { display:inline-block; padding:.4rem 1rem; border-radius:4px; text-decoration:none;
               font-weight:600; margin:.25rem; cursor:pointer; }
        .approve{ background:#22c55e; color:#fff; }
        .reject { background:#ef4444; color:#fff; }
        .snooze { background:#f59e0b; color:#000; }
        .exempt { background:#6366f1; color:#fff; }
        .meta   { font-size:.8rem; color:#94a3b8; margin-top:.5rem; }
        .badge  { display:inline-block; padding:.15rem .6rem; border-radius:9999px;
                  font-size:.75rem; font-weight:700; background:#374151; }
        table   { width:100%; border-collapse:collapse; margin-top:1rem; }
        th,td   { text-align:left; padding:.5rem; border-bottom:1px solid #334155; }
        th      { color:#94a3b8; font-size:.8rem; text-transform:uppercase; }
      </style>
    </head>
    <body>
      <h1>☁️ Cloud Cost Saver — Approval Dashboard</h1>
      <p style="color:#64748b">Phase 1 — Manual Approval Console | Refreshed: {{now}}</p>

      {% if pending %}
        <h2 style="color:#fbbf24">🔔 Pending Approvals ({{pending|length}})</h2>
        {% for vm in pending %}
        <div class="card {{vm.severity}}">
          <h3>{{vm.name}} <span class="badge">{{vm.severity}}</span>
              <span class="badge" style="background:#0f172a">{{vm.environment.upper()}}</span></h3>
          <table>
            <tr><th>Instance Type</th><td>{{vm.instance_type}}</td>
                <th>Owner Team</th><td>{{vm.owner_team}}</td></tr>
            <tr><th>Waste So Far</th><td>₹{{"{:,.2f}".format(vm.waste_so_far_inr)}}</td>
                <th>30-day Savings</th><td>${{"${:.2f}".format(vm.predicted_savings_30d_usd)}}</td></tr>
            <tr><th>Confidence</th><td>{{"{:.0%}".format(vm.decision_confidence)}}</td>
                <th>Alerted At</th><td>{{vm.alerted_at}}</td></tr>
          </table>
          <div class="meta">Why: {{ vm.explanation | join(" | ") }}</div>
          <div style="margin-top:1rem">
            <a class="btn approve" href="/approve?id={{vm.resource_id}}">✅ Approve Shutdown</a>
            <a class="btn snooze" href="/snooze?id={{vm.resource_id}}&hours=24">😴 Snooze 24h</a>
            <a class="btn exempt" href="/exempt?id={{vm.resource_id}}&days=7">🛡️ Exempt 7d</a>
            <a class="btn reject" href="/reject?id={{vm.resource_id}}">❌ Reject</a>
          </div>
        </div>
        {% endfor %}
      {% else %}
        <div class="card"><p>✅ No pending approvals — all clear!</p></div>
      {% endif %}

      <h2 style="color:#94a3b8; margin-top:2rem">📋 All Decisions</h2>
      <table>
        <tr><th>VM</th><th>Env</th><th>Severity</th><th>Status</th>
            <th>Alerted</th><th>Resolved</th><th>By</th></tr>
        {% for vm in all_vms %}
        <tr>
          <td>{{vm.name}}</td>
          <td>{{vm.environment}}</td>
          <td><span class="badge">{{vm.severity}}</span></td>
          <td>{{vm.status}}</td>
          <td style="font-size:.8rem">{{vm.alerted_at[:19] if vm.alerted_at else "—"}}</td>
          <td style="font-size:.8rem">{{vm.resolved_at[:19] if vm.resolved_at else "—"}}</td>
          <td style="font-size:.8rem">{{vm.resolved_by or "—"}}</td>
        </tr>
        {% endfor %}
      </table>
    </body>
    </html>
    """

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok", "mode": "mock" if USE_MOCK else "live"})

    @app.route("/status")
    def status():
        db      = _load_db()
        pending = get_pending()
        all_vms = sorted(db.values(), key=lambda v: v.get("alerted_at", ""), reverse=True)
        now     = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        return render_template_string(STATUS_PAGE, pending=pending, all_vms=all_vms, now=now)

    @app.route("/approve")
    def approve():
        rid = request.args.get("id")
        if not rid:
            return jsonify({"error": "missing id"}), 400

        db = _load_db()
        if rid not in db:
            return jsonify({"error": "resource not found"}), 404

        db[rid]["status"]      = "APPROVED"
        db[rid]["resolved_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        db[rid]["resolved_by"] = request.remote_addr
        _save_db(db)
        _record_outcome(db[rid], "APPROVED", request.remote_addr)

        # Trigger executor for this VM
        _execute_approved(db[rid])

        print(f"[approval] ✅  APPROVED  → {db[rid]['name']} by {request.remote_addr}")
        return (
            f"<html><body style='font-family:sans-serif;background:#0f172a;color:#e2e8f0;"
            f"display:flex;justify-content:center;align-items:center;height:100vh'>"
            f"<div style='text-align:center'><h1>✅ Approved</h1>"
            f"<p>{db[rid]['name']} has been queued for shutdown.</p>"
            f"<a href='/status' style='color:#38bdf8'>← View Dashboard</a></div></body></html>"
        )

    @app.route("/reject")
    def reject():
        rid = request.args.get("id")
        db  = _load_db()
        if rid in db:
            db[rid]["status"]      = "REJECTED"
            db[rid]["resolved_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            db[rid]["resolved_by"] = request.remote_addr
            _save_db(db)
            _record_outcome(db[rid], "REJECTED", request.remote_addr)
        print(f"[approval] ❌  REJECTED  → {db.get(rid, {}).get('name', rid)} by {request.remote_addr}")
        return (
            f"<html><body style='font-family:sans-serif;background:#0f172a;color:#e2e8f0;"
            f"display:flex;justify-content:center;align-items:center;height:100vh'>"
            f"<div style='text-align:center'><h1>❌ Rejected</h1>"
            f"<p>No action will be taken. Alert suppressed.</p>"
            f"<a href='/status' style='color:#38bdf8'>← View Dashboard</a></div></body></html>"
        )

    @app.route("/snooze")
    def snooze():
        rid   = request.args.get("id")
        hours = int(request.args.get("hours", 24))
        db    = _load_db()
        if rid in db:
            until = (datetime.datetime.utcnow() + datetime.timedelta(hours=hours)).isoformat() + "Z"
            db[rid]["status"]       = "SNOOZED"
            db[rid]["snooze_until"] = until
            db[rid]["resolved_at"]  = datetime.datetime.utcnow().isoformat() + "Z"
            db[rid]["resolved_by"]  = request.remote_addr
            _save_db(db)
            _record_outcome(db[rid], "SNOOZED", request.remote_addr)
        print(f"[approval] 😴  SNOOZED {hours}h → {db.get(rid,{}).get('name', rid)}")
        return (
            f"<html><body style='font-family:sans-serif;background:#0f172a;color:#e2e8f0;"
            f"display:flex;justify-content:center;align-items:center;height:100vh'>"
            f"<div style='text-align:center'><h1>😴 Snoozed {hours}h</h1>"
            f"<p>Alert suppressed for {hours} hours.</p>"
            f"<a href='/status' style='color:#38bdf8'>← View Dashboard</a></div></body></html>"
        )

    @app.route("/exempt")
    def exempt():
        rid  = request.args.get("id")
        days = int(request.args.get("days", 7))
        db   = _load_db()
        if rid in db:
            until = (datetime.datetime.utcnow() + datetime.timedelta(days=days)).isoformat() + "Z"
            db[rid]["status"]       = "EXEMPTED"
            db[rid]["exempt_until"] = until
            db[rid]["resolved_at"]  = datetime.datetime.utcnow().isoformat() + "Z"
            db[rid]["resolved_by"]  = request.remote_addr
            _save_db(db)
            _record_outcome(db[rid], "EXEMPTED", request.remote_addr)
        print(f"[approval] 🛡️  EXEMPT {days}d → {db.get(rid,{}).get('name', rid)}")
        return (
            f"<html><body style='font-family:sans-serif;background:#0f172a;color:#e2e8f0;"
            f"display:flex;justify-content:center;align-items:center;height:100vh'>"
            f"<div style='text-align:center'><h1>🛡️ Exempted {days} days</h1>"
            f"<p>VM will not be flagged for {days} days.</p>"
            f"<a href='/status' style='color:#38bdf8'>← View Dashboard</a></div></body></html>"
        )

    return app


def _execute_approved(entry: dict):
    """Trigger executor on an approved VM (called from approval route)."""
    try:
        from modules.executor import stop_instance
        from modules.logger   import log_action
        vm     = entry.get("vm_snapshot", {})
        result = stop_instance(vm, triggered_by="MANUAL_APPROVAL")
        log_action(result)
        print(f"[approval] ⚡  Executed approved shutdown → {entry['name']}")
    except Exception as e:
        print(f"[approval] ❌  Executor error: {e}")


# ── Escalation Watchdog ───────────────────────────────────────────────────────

def _escalation_watchdog():
    """
    Background thread that re-alerts if no ACK within ESCALATION_TIMEOUT_MINUTES.
    Runs every 60 seconds.
    """
    print(f"[escalation] 👁️  Watchdog started — checking every 60s "
          f"(timeout: {ESCALATION_TIMEOUT_MINUTES}min)")
    while True:
        time.sleep(60)
        try:
            db      = _load_db()
            now_iso = datetime.datetime.utcnow().isoformat() + "Z"
            timeout = datetime.timedelta(minutes=ESCALATION_TIMEOUT_MINUTES)

            for rid, entry in db.items():
                if entry["status"] != "PENDING" or entry.get("escalated"):
                    continue
                alerted_at = entry.get("alerted_at", "")
                if not alerted_at:
                    continue
                alerted_dt = datetime.datetime.fromisoformat(alerted_at.rstrip("Z"))
                if datetime.datetime.utcnow() - alerted_dt > timeout:
                    print(f"[escalation] 🚨  No ACK for {entry['name']} — escalating!")
                    # Re-send as escalation
                    from modules.twilio_notify import send_alert
                    vm = entry.get("vm_snapshot", {})
                    if vm:
                        send_alert(vm, escalation=True)
                    db[rid]["escalated"] = True
                    _save_db(db)
        except Exception as e:
            print(f"[escalation] ⚠️  Watchdog error: {e}")


def start_escalation_watchdog():
    """Start the escalation watchdog in a daemon thread."""
    t = threading.Thread(target=_escalation_watchdog, daemon=True, name="escalation-watchdog")
    t.start()
    return t


def start_approval_server(background: bool = True):
    """
    Start the Flask approval server.
    If background=True, runs in a daemon thread (used from main.py).
    """
    app = create_app()
    if app is None:
        return None

    print(f"\n[approval_server] 🌐  Starting on http://{APPROVAL_HOST}:{APPROVAL_PORT}")
    print(f"[approval_server] 📊  Dashboard → http://localhost:{APPROVAL_PORT}/status\n")

    if background:
        t = threading.Thread(
            target=lambda: app.run(host=APPROVAL_HOST, port=APPROVAL_PORT,
                                   debug=False, use_reloader=False),
            daemon=True, name="approval-server"
        )
        t.start()
        time.sleep(0.5)   # let Flask bind
        return t
    else:
        app.run(host=APPROVAL_HOST, port=APPROVAL_PORT, debug=False)


if __name__ == "__main__":
    start_escalation_watchdog()
    start_approval_server(background=False)
