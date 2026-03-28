"""
feedback.py
===========
Human feedback loop: approvals teach the system to tighten automation.

MVP behavior:
  - Log every APPROVE / REJECT / SNOOZE / EXEMPT with snapshot signals.
  - Per owner_team: if reject rate is high vs approve, raise confidence floor.
"""

import json
import os
import datetime
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    FEEDBACK_LOG_PATH,
    AUTO_SHUTDOWN_CONFIDENCE_FLOOR_BASE,
    FEEDBACK_WINDOW_SIZE,
    FEEDBACK_REJECT_RATIO_MAX_BUMP,
)


def _load(path: str) -> list:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(path: str, rows: list):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(rows, f, indent=2, default=str)


def record_feedback(
    resource_id: str,
    name: str,
    outcome: str,
    owner_team: str,
    idle_score: float = 0.0,
    decision_confidence: float = 0.0,
    explanation: list | None = None,
    remote_addr: str | None = None,
    extra: dict | None = None,
):
    """Append one feedback event (called from approval_server routes)."""
    row = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "resource_id": resource_id,
        "name": name,
        "outcome": outcome,
        "owner_team": owner_team,
        "idle_score": idle_score,
        "decision_confidence": decision_confidence,
        "explanation": explanation or [],
        "remote_addr": remote_addr,
        "extra": extra or {},
    }
    log = _load(FEEDBACK_LOG_PATH)
    log.append(row)
    _save(FEEDBACK_LOG_PATH, log)
    return row


def team_reject_approve_ratio(owner_team: str, window_last_n: int | None = None) -> tuple[float, float, float]:
    """
    Returns (reject_count, approve_count, reject_ratio) over last N events for team.
    If no data, ratio 0.0.
    """
    if window_last_n is None:
        window_last_n = FEEDBACK_WINDOW_SIZE
    log = _load(FEEDBACK_LOG_PATH)
    team_events = [e for e in reversed(log) if e.get("owner_team") == owner_team][:window_last_n]
    decided = [e for e in team_events if e.get("outcome") in ("REJECTED", "APPROVED")]
    if not decided:
        return 0.0, 0.0, 0.0
    rej = sum(1 for e in decided if e.get("outcome") == "REJECTED")
    app = sum(1 for e in decided if e.get("outcome") == "APPROVED")
    total_decided = rej + app
    ratio = (rej / total_decided) if total_decided else 0.0
    return float(rej), float(app), ratio


def effective_auto_shutdown_confidence_floor(owner_team: str) -> float:
    """
    Raise the confidence bar for teams that frequently reject automation
    (reduces false-positive stops).
    """
    _, _, reject_ratio = team_reject_approve_ratio(owner_team, window_last_n=FEEDBACK_WINDOW_SIZE)
    bump = min(reject_ratio * FEEDBACK_REJECT_RATIO_MAX_BUMP, FEEDBACK_REJECT_RATIO_MAX_BUMP)
    return round(min(AUTO_SHUTDOWN_CONFIDENCE_FLOOR_BASE + bump, 0.95), 4)


def summarize_feedback_teams() -> dict:
    """Lightweight summary for logging / dashboards."""
    log = _load(FEEDBACK_LOG_PATH)
    teams = {}
    for e in log:
        t = e.get("owner_team", "default")
        teams.setdefault(t, {"REJECTED": 0, "APPROVED": 0, "other": 0})
        o = e.get("outcome", "")
        if o in ("REJECTED", "APPROVED"):
            teams[t][o] += 1
        else:
            teams[t]["other"] += 1
    return teams
