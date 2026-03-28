"""
logger.py
=========
Structured JSON logging for actions, alerts, and savings.
Produces cumulative savings reports across pipeline runs.
"""

import json
import os
import datetime
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import SAVINGS_LOG_PATH, ACTION_LOG_PATH


def _load(path: str) -> list:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(path: str, data: list):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def log_action(result: dict):
    """Append a single executor result to action_log.json."""
    log = _load(ACTION_LOG_PATH)
    log.append({**result, "logged_at": datetime.datetime.utcnow().isoformat() + "Z"})
    _save(ACTION_LOG_PATH, log)


def log_savings(results: list) -> dict:
    """Persist savings session to savings_log.json."""
    log     = _load(SAVINGS_LOG_PATH)
    session = {
        "session_id"              : datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S"),
        "timestamp"               : datetime.datetime.utcnow().isoformat() + "Z",
        "vms_stopped"             : sum(1 for r in results if r.get("success")),
        "total_saved_daily_inr"   : sum(r.get("savings_daily_inr", 0)  for r in results if r.get("success")),
        "total_saved_daily_usd"   : sum(r.get("savings_daily_usd", 0)  for r in results if r.get("success")),
        "total_30d_savings_usd"   : sum(r.get("predicted_savings_30d_usd", 0) for r in results if r.get("success")),
        "waste_recovered_inr"     : sum(r.get("waste_recovered_inr", 0) for r in results if r.get("success")),
        "details"                 : results,
    }
    log.append(session)
    _save(SAVINGS_LOG_PATH, log)
    return session


def print_savings_report(results: list, all_idle_vms: list, pending_approval: list):
    """Print a terminal savings report."""
    successful     = [r for r in results if r.get("success")]
    total_daily_inr= sum(r.get("savings_daily_inr", 0) for r in successful)
    total_daily_usd= sum(r.get("savings_daily_usd", 0) for r in successful)
    total_30d_usd  = sum(r.get("predicted_savings_30d_usd", 0) for r in successful)
    waste_recovered= sum(r.get("waste_recovered_inr", 0) for r in successful)

    pending_waste  = sum(
        v.get("cost_analysis", {}).get("waste_so_far_inr", 0) for v in pending_approval
    )
    pending_30d    = sum(
        v.get("predicted_savings_30d_usd", 0) for v in pending_approval
    )

    all_sessions   = _load(SAVINGS_LOG_PATH)
    cumulative_inr = sum(s.get("total_saved_daily_inr", 0) for s in all_sessions)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'═'*65}")
    print(f"  📊  SAVINGS REPORT — {now}")
    print(f"{'═'*65}")
    print(f"  VMs total scanned          : {len(all_idle_vms) + len(results)}")
    print(f"  Idle VMs detected          : {len(all_idle_vms)}")
    print(f"  VMs auto-stopped           : {len(successful)}")
    print(f"  VMs pending approval       : {len(pending_approval)}")
    print(f"{'─'*65}")
    print(f"  Waste already recovered    : ₹{waste_recovered:>12,.2f}")
    print(f"  Daily savings (auto-stop)  : ₹{total_daily_inr:>12,.2f}  (${total_daily_usd:.2f})")
    print(f"  30-day savings forecast    :              ${total_30d_usd:>10,.2f}")
    print(f"{'─'*65}")
    print(f"  Pending approval waste     : ₹{pending_waste:>12,.2f}")
    print(f"  Potential 30d savings      :              ${pending_30d:>10,.2f}  (if approved)")
    print(f"{'─'*65}")
    print(f"  Cumulative all-time savings: ₹{cumulative_inr + total_daily_inr:>12,.2f}")
    print(f"{'═'*65}\n")
