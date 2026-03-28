"""
executor.py
===========
Safe VM Executor — stops approved idle VMs.

MOCK mode → simulates shutdown with realistic latency.
REAL mode → calls AWS EC2 stop_instances via boto3.

Safety rules:
  - Never stops prod unless explicitly approved via approval server
  - Logs every action with triggered_by (AUTO / MANUAL_APPROVAL)
  - Returns structured result for downstream logging/reporting
"""

import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import USE_MOCK
from modules.safety import evaluate_execution_safety, assert_can_stop


def stop_instance(vm: dict, triggered_by: str = "AUTO") -> dict:
    """Stop a single VM. Returns a structured result dict."""
    try:
        assert_can_stop(vm, triggered_by=triggered_by)
    except RuntimeError as e:
        print(f"[executor] ⛔  Safety blocked: {e}")
        return {
            "success": False,
            "resource_id": vm.get("resource_id"),
            "instance_name": vm.get("name"),
            "error": str(e),
            "safety": evaluate_execution_safety(vm, triggered_by=triggered_by),
            "mock": USE_MOCK,
        }

    if USE_MOCK:
        result = _mock_stop(vm)
    else:
        result = _real_stop(vm)

    icon = "✅" if result["success"] else "❌"
    ca   = vm.get("cost_analysis", {})
    print(f"[executor] {icon}  {'Stopped' if result['success'] else 'FAILED'}: "
          f"{vm['name']} ({vm['resource_id']})  "
          f"Daily savings: ₹{ca.get('daily_waste_inr', 0):,.0f}")
    return result


def _mock_stop(vm: dict) -> dict:
    """Simulated shutdown — no real API calls."""
    import time
    import random
    time.sleep(random.uniform(0.2, 0.6))   # realistic API latency

    ca  = vm.get("cost_analysis", {})
    dec = vm.get("decision", {})
    return {
        "success"                   : True,
        "resource_id"               : vm["resource_id"],
        "instance_name"             : vm["name"],
        "instance_type"             : vm.get("instance_type"),
        "environment"               : vm.get("environment"),
        "action"                    : "STOPPED",
        "previous_state"            : "running",
        "new_state"                 : "stopped",
        "stopped_at"                : datetime.datetime.utcnow().isoformat() + "Z",
        "savings_daily_inr"         : ca.get("daily_waste_inr", 0),
        "savings_daily_usd"         : ca.get("daily_waste_usd", 0),
        "predicted_savings_30d_usd" : ca.get("predicted_savings_30d_usd", 0),
        "waste_recovered_inr"       : ca.get("waste_so_far_inr", 0),
        "triggered_by"              : dec.get("action", "AUTO"),
        "decision_confidence"       : vm.get("decision_confidence", 0),
        "explanation"               : vm.get("idle_analysis", {}).get("explanation", []),
        "mock"                      : True,
    }


def _real_stop(vm: dict) -> dict:
    """Call AWS EC2 API to stop the instance."""
    try:
        import boto3
        from config.settings import AWS_ACCESS_KEY, AWS_SECRET_KEY, REGION
        ec2  = boto3.client("ec2", region_name=REGION,
                             aws_access_key_id=AWS_ACCESS_KEY,
                             aws_secret_access_key=AWS_SECRET_KEY)
        resp = ec2.stop_instances(InstanceIds=[vm["resource_id"]])
        new_state = resp["StoppingInstances"][0]["CurrentState"]["Name"]
        ca        = vm.get("cost_analysis", {})
        dec       = vm.get("decision", {})
        return {
            "success"                   : True,
            "resource_id"               : vm["resource_id"],
            "instance_name"             : vm["name"],
            "instance_type"             : vm.get("instance_type"),
            "environment"               : vm.get("environment"),
            "action"                    : "STOPPED",
            "new_state"                 : new_state,
            "stopped_at"                : datetime.datetime.utcnow().isoformat() + "Z",
            "savings_daily_inr"         : ca.get("daily_waste_inr", 0),
            "savings_daily_usd"         : ca.get("daily_waste_usd", 0),
            "predicted_savings_30d_usd" : ca.get("predicted_savings_30d_usd", 0),
            "waste_recovered_inr"       : ca.get("waste_so_far_inr", 0),
            "triggered_by"              : dec.get("action", "MANUAL"),
            "decision_confidence"       : vm.get("decision_confidence", 0),
            "mock"                      : False,
        }
    except Exception as e:
        return {
            "success"     : False,
            "resource_id" : vm["resource_id"],
            "instance_name": vm.get("name"),
            "error"       : str(e),
            "mock"        : False,
        }


def execute_all(auto_shutdown_vms: list) -> list:
    """Run shutdown on all auto-approved VMs, return results."""
    print(f"\n[executor] ⚡  Executing {len(auto_shutdown_vms)} auto-shutdowns...")
    results = [stop_instance(vm, triggered_by="AUTO") for vm in auto_shutdown_vms]
    saved_inr   = sum(r.get("savings_daily_inr", 0) for r in results if r["success"])
    saved_usd   = sum(r.get("savings_daily_usd", 0) for r in results if r["success"])
    failed      = sum(1 for r in results if not r["success"])
    print(f"[executor] 💰  Done: {len(results)-failed} stopped, {failed} failed  "
          f"| Daily savings: ₹{saved_inr:,.0f}  (${saved_usd:.2f})")
    return results
