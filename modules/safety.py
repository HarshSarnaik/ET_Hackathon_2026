"""
safety.py
=========
Central safety gate before any destructive action.

Separates detection output from execution: even if a VM was scored as waste,
`evaluate_execution_safety` decides whether stopping is allowed.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import settings
from modules.feedback import effective_auto_shutdown_confidence_floor


def _protected_keywords_block(vm: dict) -> bool:
    name_lower = vm.get("name", "").lower()
    return any(kw in name_lower for kw in settings.PROTECTED_KEYWORDS)


def _protected_tag(vm: dict) -> bool:
    crit = vm.get("tags", {}).get("criticality", "").lower()
    return crit in ("high", "critical")


def _cost_saver_exempt_tag(vm: dict) -> bool:
    v = str(vm.get("tags", {}).get("CostSaverExempt", "")).lower()
    return v in ("true", "yes", "1")


def _activity_veto_active(vm: dict) -> bool:
    ia = vm.get("idle_analysis") or {}
    return bool(ia.get("has_activity_veto"))


def evaluate_execution_safety(vm: dict, triggered_by: str = "AUTO") -> dict:
    """
    triggered_by:
      AUTO — pipeline auto path (strict)
      MANUAL_APPROVAL — user clicked Approve (skips confidence / decision.action checks)
    """
    blockers = []
    env = (vm.get("environment") or "prod").lower()
    dec = vm.get("decision") or {}
    action = dec.get("action", "NOTIFY_TWILIO")
    confidence = float(vm.get("decision_confidence") or 0.0)
    floor = effective_auto_shutdown_confidence_floor(vm.get("owner_team", "default"))
    manual = triggered_by == "MANUAL_APPROVAL"

    if not settings.AUTO_SHUTDOWN_MASTER_ENABLE:
        blockers.append("AUTO_SHUTDOWN_MASTER_ENABLE is False — stops disabled globally.")

    if settings.EXECUTION_DRY_RUN:
        blockers.append("EXECUTION_DRY_RUN is True — no real stops.")

    if _cost_saver_exempt_tag(vm):
        blockers.append("CostSaverExempt tag — owner opted out of stops.")

    if _activity_veto_active(vm):
        blockers.append(
            "Sustained disk I/O or network activity on snapshot — not a safe stop target."
        )

    if _protected_keywords_block(vm):
        blockers.append("Name matches protected workload keyword — stop blocked.")

    if _protected_tag(vm):
        blockers.append("criticality tag high/critical — stop blocked.")

    if env == "prod" and not manual and not settings.ALLOW_AUTO_SHUTDOWN_IN_PROD:
        blockers.append("Production: auto-shutdown disabled; use approval workflow.")

    if env == "prod" and manual and not settings.ALLOW_MANUAL_STOP_IN_PROD:
        blockers.append("ALLOW_MANUAL_STOP_IN_PROD is False — even approved stops blocked in prod.")

    if not manual:
        if action != "AUTO_SHUTDOWN":
            blockers.append(f"Decision action is {action}, not AUTO_SHUTDOWN.")
        if confidence < floor:
            blockers.append(
                f"Confidence {confidence:.0%} below team floor {floor:.0%} (see feedback loop)."
            )

    allowed = len(blockers) == 0

    return {
        "allowed": allowed,
        "blockers": blockers,
        "confidence_floor": floor,
        "triggered_by": triggered_by,
    }


def assert_can_stop(vm: dict, triggered_by: str = "AUTO") -> None:
    r = evaluate_execution_safety(vm, triggered_by=triggered_by)
    if not r["allowed"]:
        raise RuntimeError("Stop blocked: " + " | ".join(r["blockers"]))
