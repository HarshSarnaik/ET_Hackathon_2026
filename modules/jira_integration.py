"""
modules/jira_integration.py
============================
Phase 2 — Jira ticket creation for VMs needing human approval.

MOCK mode: simulates ticket creation with realistic output.
LIVE mode: would call Jira REST API (not implemented yet).

Usage:
    from modules.jira_integration import create_tickets_for_batch
    jira_map = create_tickets_for_batch(notify_list)
"""

import sys
import os
import random
import string
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    from config.settings import JIRA_ENABLED
except ImportError:
    JIRA_ENABLED = True

try:
    from config.settings import JIRA_PROJECT_KEY
except ImportError:
    JIRA_PROJECT_KEY = "CLOUD"


def _generate_ticket_key() -> str:
    """Generate a mock Jira ticket key (e.g. CLOUD-1234)."""
    num = random.randint(1000, 9999)
    return f"{JIRA_PROJECT_KEY}-{num}"


def _build_description(vm: dict) -> str:
    """Build a rich ticket description from VM data."""
    ca = vm.get("cost_analysis", {})
    ia = vm.get("idle_analysis", {})
    dec = vm.get("decision", {})

    lines = [
        f"*VM:* {vm.get('name', 'unknown')}",
        f"*Resource ID:* {vm.get('resource_id', 'N/A')}",
        f"*Instance Type:* {vm.get('instance_type', 'N/A')}",
        f"*Environment:* {vm.get('environment', 'N/A')}",
        f"*Owner Team:* {vm.get('owner_team', 'N/A')}",
        f"*Region:* {vm.get('region', 'N/A')}",
        "",
        f"*Severity:* {ca.get('severity', 'LOW')}",
        f"*Confidence:* {vm.get('decision_confidence', 0):.0%}",
        f"*Daily Waste:* ₹{ca.get('daily_waste_inr', 0):,.0f} (${ca.get('daily_waste_usd', 0):.2f})",
        f"*Waste So Far:* ₹{ca.get('waste_so_far_inr', 0):,.0f}",
        f"*30-day Savings:* ${ca.get('predicted_savings_30d_usd', 0):,.2f}",
        "",
        "*Idle Analysis:*",
    ]

    explanation = ia.get("explanation", [])
    for e in explanation:
        lines.append(f"  • {e}")

    if dec.get("reason"):
        lines.append(f"\n*Decision Reason:* {dec['reason']}")

    return "\n".join(lines)


def _severity_to_priority(severity: str) -> str:
    """Map cost severity to Jira priority."""
    return {
        "CRITICAL": "Highest",
        "HIGH": "High",
        "MEDIUM": "Medium",
        "LOW": "Low",
    }.get(severity, "Medium")


def create_ticket(vm: dict) -> dict:
    """Create a single Jira ticket for a VM (mock)."""
    if not JIRA_ENABLED:
        return {"created": False, "reason": "JIRA_ENABLED is False"}

    ticket_key = _generate_ticket_key()
    ca = vm.get("cost_analysis", {})
    severity = ca.get("severity", "LOW")

    ticket = {
        "key": ticket_key,
        "project": JIRA_PROJECT_KEY,
        "summary": f"[Cloud Cost] Idle VM: {vm.get('name', 'unknown')} "
                   f"({vm.get('environment', '?')}) — {severity}",
        "description": _build_description(vm),
        "priority": _severity_to_priority(severity),
        "labels": ["cloud-cost", "idle-vm", vm.get("environment", "unknown")],
        "assignee": vm.get("owner_team", "unassigned"),
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "created": True,
        "mock": True,
    }

    sev_icon = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(severity, "⚠️")
    print(f"  [jira] {sev_icon} Created {ticket_key}: {vm.get('name', '?')} [{severity}]")

    return ticket


def create_tickets_for_batch(vms: list) -> dict:
    """
    Create Jira tickets for a list of VMs.
    Returns: {resource_id: ticket_key}
    """
    if not JIRA_ENABLED:
        print("  [jira] ⏭️  Jira integration disabled (JIRA_ENABLED=False)")
        return {}

    print(f"  [jira] 📋  Creating tickets for {len(vms)} VMs...")
    jira_map = {}

    for vm in vms:
        ticket = create_ticket(vm)
        if ticket.get("created"):
            rid = vm.get("resource_id", "")
            jira_map[rid] = ticket["key"]
            vm["jira_ticket"] = ticket["key"]

    created = sum(1 for v in jira_map.values() if v)
    print(f"  [jira] ✅  {created} tickets created ({JIRA_PROJECT_KEY} project)")
    return jira_map
