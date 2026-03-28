"""
db/store.py
===========
Phase 2 — SQLite persistence layer.

All pipeline data (runs, VMs, detections, actions, approvals, alerts,
feedback, ML scores) is persisted to a local SQLite database.

Usage:
    from db.store import init_db, start_run, finish_run, ...
"""

import sqlite3
import uuid
import json
import os
import datetime
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DB_DIR  = os.path.join(os.path.dirname(__file__))
DB_PATH = os.path.join(DB_DIR, "cloud_cost_saver.db")

_local = threading.local()


def _conn() -> sqlite3.Connection:
    """Thread-local SQLite connection with WAL mode."""
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(DB_DIR, exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    mode         TEXT NOT NULL DEFAULT 'mock',
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS vms (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    resource_id  TEXT NOT NULL,
    name         TEXT,
    instance_type TEXT,
    environment  TEXT,
    owner_team   TEXT,
    region       TEXT,
    snapshot_json TEXT,
    updated_at   TEXT NOT NULL,
    UNIQUE(run_id, resource_id)
);

CREATE TABLE IF NOT EXISTS detections (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    resource_id  TEXT NOT NULL,
    idle_score   REAL,
    confidence   REAL,
    signals_json TEXT,
    detected_at  TEXT NOT NULL,
    UNIQUE(run_id, resource_id)
);

CREATE TABLE IF NOT EXISTS actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    resource_id  TEXT NOT NULL,
    action       TEXT,
    success      INTEGER,
    savings_daily_inr  REAL DEFAULT 0,
    savings_daily_usd  REAL DEFAULT 0,
    result_json  TEXT,
    executed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    resource_id  TEXT NOT NULL,
    name         TEXT,
    status       TEXT DEFAULT 'PENDING',
    severity     TEXT,
    registered_at TEXT NOT NULL,
    resolved_at  TEXT,
    resolved_by  TEXT,
    vm_json      TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    resource_id  TEXT NOT NULL,
    channel      TEXT,
    sent         INTEGER DEFAULT 0,
    alert_json   TEXT,
    sent_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id  TEXT NOT NULL,
    run_id       TEXT,
    outcome      TEXT NOT NULL,
    vm_json      TEXT,
    was_correct  INTEGER,
    recorded_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ml_scores (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    resource_id  TEXT NOT NULL,
    isolation_score REAL,
    waste_score_30d REAL,
    ml_rank      INTEGER,
    scored_at    TEXT NOT NULL,
    UNIQUE(run_id, resource_id)
);
"""


def init_db():
    """Create all tables if they don't exist."""
    conn = _conn()
    conn.executescript(SCHEMA)
    conn.commit()
    print(f"  [db] ✅  Database initialized at {DB_PATH}")


def _now() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _json(obj) -> str:
    return json.dumps(obj, default=str)


# ── Runs ──────────────────────────────────────────────────────────────────────

def start_run(mode: str = "mock") -> str:
    """Begin a new pipeline run. Returns a correlation UUID."""
    run_id = str(uuid.uuid4())[:12]
    conn = _conn()
    conn.execute(
        "INSERT INTO runs (run_id, mode, started_at) VALUES (?, ?, ?)",
        (run_id, mode, _now()),
    )
    conn.commit()
    return run_id


def finish_run(run_id: str, summary: dict):
    """Mark a run as complete with a summary dict."""
    conn = _conn()
    conn.execute(
        "UPDATE runs SET finished_at=?, summary_json=? WHERE run_id=?",
        (_now(), _json(summary), run_id),
    )
    conn.commit()


# ── VMs ───────────────────────────────────────────────────────────────────────

def upsert_vm(run_id: str, vm: dict):
    """Insert or update a VM snapshot for this run."""
    conn = _conn()
    conn.execute(
        """INSERT INTO vms (run_id, resource_id, name, instance_type, environment,
                            owner_team, region, snapshot_json, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(run_id, resource_id) DO UPDATE SET
               snapshot_json=excluded.snapshot_json, updated_at=excluded.updated_at""",
        (
            run_id,
            vm.get("resource_id", ""),
            vm.get("name", ""),
            vm.get("instance_type", ""),
            vm.get("environment", ""),
            vm.get("owner_team", ""),
            vm.get("region", ""),
            _json(vm),
            _now(),
        ),
    )
    conn.commit()


# ── Detections ────────────────────────────────────────────────────────────────

def upsert_detection(run_id: str, vm: dict):
    """Record an idle detection event."""
    ia = vm.get("idle_analysis") or {}
    conn = _conn()
    conn.execute(
        """INSERT INTO detections (run_id, resource_id, idle_score, confidence,
                                   signals_json, detected_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(run_id, resource_id) DO UPDATE SET
               idle_score=excluded.idle_score, confidence=excluded.confidence,
               signals_json=excluded.signals_json, detected_at=excluded.detected_at""",
        (
            run_id,
            vm.get("resource_id", ""),
            vm.get("idle_score", 0),
            vm.get("decision_confidence", 0),
            _json(ia.get("signals", {})),
            _now(),
        ),
    )
    conn.commit()


# ── Actions ───────────────────────────────────────────────────────────────────

def record_action(run_id: str, vm: dict, result: dict):
    """Record an execution action (stop / fail)."""
    conn = _conn()
    conn.execute(
        """INSERT INTO actions (run_id, resource_id, action, success,
                                savings_daily_inr, savings_daily_usd,
                                result_json, executed_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            run_id,
            vm.get("resource_id", ""),
            result.get("action", "STOP"),
            1 if result.get("success") else 0,
            result.get("savings_daily_inr", 0),
            result.get("savings_daily_usd", 0),
            _json(result),
            _now(),
        ),
    )
    conn.commit()


# ── Approvals ─────────────────────────────────────────────────────────────────

def register_approval(run_id: str, vm: dict):
    """Register a VM as pending human approval."""
    conn = _conn()
    conn.execute(
        """INSERT INTO approvals (run_id, resource_id, name, status, severity,
                                  registered_at, vm_json)
           VALUES (?,?,?,?,?,?,?)""",
        (
            run_id,
            vm.get("resource_id", ""),
            vm.get("name", ""),
            "PENDING",
            vm.get("cost_analysis", {}).get("severity", "LOW"),
            _now(),
            _json(vm),
        ),
    )
    conn.commit()


# ── Alerts ────────────────────────────────────────────────────────────────────

def record_alert(run_id: str, resource_id: str, alert_result: dict):
    """Record a Twilio alert send attempt."""
    conn = _conn()
    conn.execute(
        """INSERT INTO alerts (run_id, resource_id, channel, sent, alert_json, sent_at)
           VALUES (?,?,?,?,?,?)""",
        (
            run_id,
            resource_id,
            alert_result.get("channel", "unknown"),
            1 if alert_result.get("sent") else 0,
            _json(alert_result),
            _now(),
        ),
    )
    conn.commit()


# ── Feedback ──────────────────────────────────────────────────────────────────

def record_feedback(resource_id: str, run_id: str, outcome: str,
                    vm: dict = None, was_correct: bool = None):
    """Record a feedback event (approve / reject / auto-correct)."""
    conn = _conn()
    conn.execute(
        """INSERT INTO feedback (resource_id, run_id, outcome, vm_json,
                                 was_correct, recorded_at)
           VALUES (?,?,?,?,?,?)""",
        (
            resource_id,
            run_id,
            outcome,
            _json(vm) if vm else "{}",
            1 if was_correct else (0 if was_correct is False else None),
            _now(),
        ),
    )
    conn.commit()


# ── ML Scores ─────────────────────────────────────────────────────────────────

def upsert_ml_score(run_id: str, resource_id: str, scores: dict):
    """Record ML ranking scores for a VM."""
    conn = _conn()
    conn.execute(
        """INSERT INTO ml_scores (run_id, resource_id, isolation_score,
                                   waste_score_30d, ml_rank, scored_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(run_id, resource_id) DO UPDATE SET
               isolation_score=excluded.isolation_score,
               waste_score_30d=excluded.waste_score_30d,
               ml_rank=excluded.ml_rank, scored_at=excluded.scored_at""",
        (
            run_id,
            resource_id,
            scores.get("isolation_score"),
            scores.get("waste_score_30d"),
            scores.get("ml_rank"),
            _now(),
        ),
    )
    conn.commit()


# ── Aggregates ────────────────────────────────────────────────────────────────

def get_cumulative_savings() -> dict:
    """Sum of all successful action savings across all runs."""
    conn = _conn()
    row = conn.execute(
        """SELECT COALESCE(SUM(savings_daily_inr),0) AS total_daily_inr,
                  COALESCE(SUM(savings_daily_usd),0) AS total_daily_usd
           FROM actions WHERE success=1"""
    ).fetchone()
    return {
        "total_daily_inr": row["total_daily_inr"],
        "total_daily_usd": row["total_daily_usd"],
    }


def get_precision_stats() -> dict:
    """Compute a precision proxy from feedback: correct / total."""
    conn = _conn()
    total = conn.execute("SELECT COUNT(*) AS n FROM feedback").fetchone()["n"]
    correct = conn.execute(
        "SELECT COUNT(*) AS n FROM feedback WHERE was_correct=1"
    ).fetchone()["n"]
    if total == 0:
        return {"precision_proxy": None, "total": 0, "correct": 0}
    return {
        "precision_proxy": round(correct / total, 4),
        "total": total,
        "correct": correct,
    }
