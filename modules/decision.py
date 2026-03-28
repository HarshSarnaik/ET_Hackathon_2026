"""
modules/decision.py
===================
Phase 2 — Policy-aware Decision Engine (upgraded)

Now delegates to policy_engine.py for all rule evaluation.
Adds: freeze window checks, blast radius limits, dry-run mode,
tag-rule overrides, maintenance window awareness.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from modules.policy_engine import evaluate, record_auto_stop, reset_run_counters, print_policy_summary
from modules.observability  import update as obs_update

AUTO_SHUTDOWN_CONFIDENCE_FLOOR = 0.70

def decide(vm: dict, run_id: str = None) -> dict:
    pol = evaluate(vm, run_id)
    action   = pol["action"]
    severity = vm.get("cost_analysis", {}).get("severity", "LOW")
    waste    = vm.get("cost_analysis", {}).get("waste_so_far_inr", 0)

    if pol.get("in_freeze_window") and action == "AUTO_SHUTDOWN":
        obs_update("vms_blocked_freeze_total", 1, delta=True)
    if pol.get("blast_radius_exceeded"):
        obs_update("vms_blocked_blast_total", 1, delta=True)

    vm["decision"] = {
        "action"               : action,
        "requires_approval"    : pol["requires_approval"],
        "allowed_actions"      : pol["allowed_actions"],
        "reason"               : pol["reason"],
        "explanation"          : vm.get("idle_analysis", {}).get("explanation", []),
        "priority"             : {"CRITICAL":1,"HIGH":2,"MEDIUM":3,"LOW":4}.get(severity, 4),
        "protected"            : pol["protected"],
        "severity"             : severity,
        "confidence"           : vm.get("decision_confidence", 0),
        "in_freeze_window"     : pol["in_freeze_window"],
        "in_maintenance_window": pol["in_maintenance_window"],
        "dry_run"              : pol["dry_run"],
        "jira_required"        : pol["jira_required"],
        "policy"               : pol,
    }
    return vm

def decide_all(idle_vms: list, run_id: str = None) -> tuple:
    reset_run_counters()
    print_policy_summary()
    decided     = [decide(vm, run_id) for vm in idle_vms]
    auto_list   = sorted([v for v in decided if v["decision"]["action"] == "AUTO_SHUTDOWN"],
                         key=lambda v: v["decision"]["priority"])
    notify_list = sorted([v for v in decided if v["decision"]["action"] == "NOTIFY_TWILIO"],
                         key=lambda v: v["decision"]["priority"])
    skip_list   = [v for v in decided if v["decision"]["action"] in ("SKIP","DRY_RUN")]

    _print_decisions(auto_list, notify_list, skip_list)
    return auto_list, notify_list

def _print_decisions(auto_list, notify_list, skip_list):
    print(f"\n[decision] 🤖  Policy Engine v2 Results\n")
    print(f"  ⚡  AUTO-SHUTDOWN ({len(auto_list)} VMs):")
    for v in auto_list:
        d = v["decision"]
        print(f"     ✅ {v['name']:<28}  conf:{d['confidence']:.0%}  freeze:{d['in_freeze_window']}")
    if not auto_list: print("     (none)")

    print(f"\n  🔔  NEEDS TWILIO APPROVAL ({len(notify_list)} VMs):")
    for v in notify_list:
        d = v["decision"]
        icon = {"CRITICAL":"🚨","HIGH":"🔴","MEDIUM":"🟡","LOW":"🟢"}.get(d["severity"],"⚠️")
        jira = "📋" if d.get("jira_required") else ""
        print(f"     {icon} {v['name']:<28}  [{d['severity']}]  conf:{d['confidence']:.0%}  {jira}")
    if not notify_list: print("     (none)")

    if skip_list:
        print(f"\n  ⏭️   SKIPPED / DRY-RUN ({len(skip_list)} VMs):")
        for v in skip_list:
            print(f"     ○ {v['name']:<28}  {v['decision']['reason'][:60]}")
    print()
