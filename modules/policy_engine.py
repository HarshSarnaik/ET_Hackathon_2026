"""
modules/policy_engine.py
========================
Phase 2 — Policy Engine v2.

Centralized rule evaluation for the decision engine. Adds:
  - Freeze window checks (no auto-shutdown during deploy windows)
  - Blast radius limits (cap simultaneous auto-shutdowns per run)
  - Dry-run mode (log but don't act)
  - Tag-rule overrides (e.g. CostSaverPolicy=aggressive → always auto)
  - Maintenance window awareness

Called by decision.py: evaluate(vm, run_id) → policy dict
"""

import sys
import os
import datetime
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    ENVIRONMENT_POLICY,
    PROTECTED_KEYWORDS,
    AUTO_SHUTDOWN_MASTER_ENABLE,
)

# ── Run-level counters (thread-safe) ─────────────────────────────────────────
_lock = threading.Lock()
_run_auto_count = 0

# ── Configurable limits (import-safe defaults if settings doesn't have them) ─
try:
    from config.settings import BLAST_RADIUS_LIMIT
except ImportError:
    BLAST_RADIUS_LIMIT = 5

try:
    from config.settings import DRY_RUN_MODE
except ImportError:
    DRY_RUN_MODE = False

try:
    from config.settings import FREEZE_WINDOWS
except ImportError:
    FREEZE_WINDOWS = []

try:
    from config.settings import MAINTENANCE_WINDOWS
except ImportError:
    MAINTENANCE_WINDOWS = []

try:
    from config.settings import JIRA_ENABLED
except ImportError:
    JIRA_ENABLED = True

try:
    from config.settings import AUTO_SHUTDOWN_CONFIDENCE_FLOOR_BASE
except ImportError:
    AUTO_SHUTDOWN_CONFIDENCE_FLOOR_BASE = 0.70


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc():
    return datetime.datetime.utcnow()


def _in_window(windows: list) -> bool:
    """Check if current time falls within any configured window.
    Each window is a dict: {"start": "HH:MM", "end": "HH:MM", "days": [0-6]}
    Days: 0=Mon, 6=Sun.  If no days key, applies every day.
    """
    now = _now_utc()
    now_time = now.strftime("%H:%M")
    now_dow = now.weekday()
    for w in windows:
        days = w.get("days")
        if days is not None and now_dow not in days:
            continue
        start = w.get("start", "00:00")
        end = w.get("end", "23:59")
        if start <= now_time <= end:
            return True
    return False


def _is_protected(vm: dict) -> bool:
    name_lower = vm.get("name", "").lower()
    tags = vm.get("tags", {})
    criticality = tags.get("criticality", "").lower()
    if any(kw in name_lower for kw in PROTECTED_KEYWORDS):
        return True
    if criticality in ("high", "critical"):
        return True
    return False


def _tag_override(vm: dict) -> str | None:
    """Check for CostSaverPolicy tag override.
    Possible values: aggressive, conservative, exempt, skip.
    """
    policy_tag = vm.get("tags", {}).get("CostSaverPolicy", "").lower()
    if policy_tag in ("aggressive", "auto"):
        return "AUTO_SHUTDOWN"
    if policy_tag in ("conservative", "notify"):
        return "NOTIFY_TWILIO"
    if policy_tag in ("exempt", "skip"):
        return "SKIP"
    return None


# ── Core ──────────────────────────────────────────────────────────────────────

def evaluate(vm: dict, run_id: str = None) -> dict:
    """
    Evaluate all policy rules for a VM.
    Returns a policy dict consumed by decision.py.
    """
    global _run_auto_count

    env = (vm.get("environment") or "prod").lower()
    base_policy = ENVIRONMENT_POLICY.get(env, ENVIRONMENT_POLICY.get("prod", {}))
    action = base_policy.get("action", "NOTIFY_TWILIO")
    confidence = float(vm.get("decision_confidence", 0))
    protected = _is_protected(vm)
    reasons = []

    in_freeze = _in_window(FREEZE_WINDOWS)
    in_maintenance = _in_window(MAINTENANCE_WINDOWS)
    blast_exceeded = False
    dry_run = DRY_RUN_MODE

    # 1. Master kill-switch
    if not AUTO_SHUTDOWN_MASTER_ENABLE and action == "AUTO_SHUTDOWN":
        action = "NOTIFY_TWILIO"
        reasons.append("AUTO_SHUTDOWN_MASTER_ENABLE is off → NOTIFY")

    # 2. Tag override
    tag_action = _tag_override(vm)
    if tag_action == "SKIP":
        action = "SKIP"
        reasons.append("CostSaverPolicy tag = exempt/skip → SKIP")
    elif tag_action is not None and not protected:
        action = tag_action
        reasons.append(f"CostSaverPolicy tag override → {tag_action}")

    # 3. Protected resource
    if action == "AUTO_SHUTDOWN" and protected:
        action = "NOTIFY_TWILIO"
        reasons.append("protected resource → NOTIFY")

    # 4. Activity veto
    ia = vm.get("idle_analysis") or {}
    if action == "AUTO_SHUTDOWN" and ia.get("has_activity_veto"):
        action = "NOTIFY_TWILIO"
        reasons.append("activity veto present → NOTIFY")

    # 5. Confidence floor
    if action == "AUTO_SHUTDOWN" and confidence < AUTO_SHUTDOWN_CONFIDENCE_FLOOR_BASE:
        action = "NOTIFY_TWILIO"
        reasons.append(f"confidence {confidence:.0%} < floor {AUTO_SHUTDOWN_CONFIDENCE_FLOOR_BASE:.0%} → NOTIFY")

    # 6. Freeze window
    if action == "AUTO_SHUTDOWN" and in_freeze:
        action = "NOTIFY_TWILIO"
        reasons.append("freeze window active → NOTIFY (no auto-shutdown during freeze)")

    # 7. Maintenance window — prefer notification over auto
    if action == "AUTO_SHUTDOWN" and in_maintenance:
        action = "NOTIFY_TWILIO"
        reasons.append("maintenance window active → NOTIFY")

    # 8. Blast radius limit
    with _lock:
        if action == "AUTO_SHUTDOWN":
            if _run_auto_count >= BLAST_RADIUS_LIMIT:
                action = "NOTIFY_TWILIO"
                blast_exceeded = True
                reasons.append(f"blast radius limit ({BLAST_RADIUS_LIMIT}) reached → NOTIFY")

    # 9. Dry-run mode
    if dry_run and action == "AUTO_SHUTDOWN":
        action = "DRY_RUN"
        reasons.append("DRY_RUN_MODE enabled → log only")

    if not reasons:
        reasons.append(f"standard policy for {env} environment")

    # Determine allowed actions and Jira requirement
    requires_approval = action in ("NOTIFY_TWILIO",)
    allowed_actions = ["NOTIFY_TWILIO"]
    if not protected and env != "prod":
        allowed_actions.append("AUTO_SHUTDOWN")
    if env == "prod":
        allowed_actions.append("MANUAL_APPROVAL")

    severity = vm.get("cost_analysis", {}).get("severity", "LOW")
    jira_required = JIRA_ENABLED and severity in ("CRITICAL", "HIGH") and requires_approval

    return {
        "action": action,
        "requires_approval": requires_approval,
        "allowed_actions": allowed_actions,
        "reason": " | ".join(reasons),
        "protected": protected,
        "in_freeze_window": in_freeze,
        "in_maintenance_window": in_maintenance,
        "blast_radius_exceeded": blast_exceeded,
        "dry_run": dry_run and action == "DRY_RUN",
        "jira_required": jira_required,
        "confidence_floor": AUTO_SHUTDOWN_CONFIDENCE_FLOOR_BASE,
        "environment_policy": env,
    }


def record_auto_stop():
    """Increment the blast-radius counter after a successful auto-stop."""
    global _run_auto_count
    with _lock:
        _run_auto_count += 1


def reset_run_counters():
    """Reset per-run counters (call at start of each pipeline run)."""
    global _run_auto_count
    with _lock:
        _run_auto_count = 0


def print_policy_summary():
    """Print active policy configuration."""
    print(f"  [policy] ⚙️  Policy Engine v2 Configuration:")
    print(f"  [policy]   Master auto-shutdown : {'ON' if AUTO_SHUTDOWN_MASTER_ENABLE else 'OFF'}")
    print(f"  [policy]   Blast radius limit   : {BLAST_RADIUS_LIMIT} VMs/run")
    print(f"  [policy]   Dry-run mode         : {'ON' if DRY_RUN_MODE else 'OFF'}")
    print(f"  [policy]   Freeze windows       : {len(FREEZE_WINDOWS)} configured")
    print(f"  [policy]   Maintenance windows  : {len(MAINTENANCE_WINDOWS)} configured")
    print(f"  [policy]   Jira integration     : {'ON' if JIRA_ENABLED else 'OFF'}")
    print(f"  [policy]   Confidence floor     : {AUTO_SHUTDOWN_CONFIDENCE_FLOOR_BASE:.0%}")
