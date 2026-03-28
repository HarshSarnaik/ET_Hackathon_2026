"""
detect_idle.py
==============
WASTE detection only (see config/policies.py for waste vs action split).

Multi-signal idle scoring plus hard ACTIVITY VETO:
  Low CPU alone cannot qualify if disk I/O, IOPS, or network show sustained work
  (typical DB / cache / streaming pattern).

Outputs idle_analysis:
  - explanation / veto_explanation — "why flagged" or "why excluded"
  - has_activity_veto — blocks automation downstream (safety.py)
  - all_signals_idle — all dimensions quiet (strict gate)
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    VM_DATA_PATH,
    CPU_IDLE_THRESHOLD,
    GPU_IDLE_THRESHOLD,
    RAM_IDLE_THRESHOLD,
    NETWORK_IDLE_THRESHOLD,
    STORAGE_IDLE_THRESHOLD,
    IDLE_DURATION_HOURS,
    SIGNAL_WEIGHTS,
    IDLE_CONFIDENCE_THRESHOLD,
    ACTIVITY_VETO_DISK_IOPS,
    ACTIVITY_VETO_STORAGE_MBPS,
    ACTIVITY_VETO_NETWORK_MBPS,
)
from modules.approval_server import is_exempt


def _activity_veto_explanations(vm: dict) -> tuple[list[str], bool]:
    """Hard veto when workload is clearly active on non-CPU dimensions."""
    reasons = []
    iops = int(vm.get("disk_iops") or 0)
    stor = float(vm.get("storage_read_mbps") or 0) + float(vm.get("storage_write_mbps") or 0)
    net = float(vm.get("network_in_mbps") or 0) + float(vm.get("network_out_mbps") or 0)

    if iops >= ACTIVITY_VETO_DISK_IOPS:
        reasons.append(
            f"VETO: disk_iops {iops}>={ACTIVITY_VETO_DISK_IOPS} — sustained storage load (often DB / stateful)"
        )
    if stor >= ACTIVITY_VETO_STORAGE_MBPS:
        reasons.append(
            f"VETO: storage throughput {stor:.2f} MB/s>={ACTIVITY_VETO_STORAGE_MBPS} MB/s — not IO-idle"
        )
    if net >= ACTIVITY_VETO_NETWORK_MBPS:
        reasons.append(
            f"VETO: network {net:.2f} MB/s>={ACTIVITY_VETO_NETWORK_MBPS} MB/s — traffic present"
        )
    return reasons, len(reasons) > 0


def _compute_idle_score(vm: dict) -> dict:
    has_gpu = vm.get("has_gpu", False)

    signals = {
        "cpu_idle": vm["cpu_usage_pct"] < CPU_IDLE_THRESHOLD,
        "ram_idle": vm["memory_usage_pct"] < RAM_IDLE_THRESHOLD,
        "gpu_idle": (vm["gpu_usage_pct"] < GPU_IDLE_THRESHOLD) if has_gpu else None,
        "network_idle": (vm["network_in_mbps"] + vm.get("network_out_mbps", 0)) < NETWORK_IDLE_THRESHOLD,
        "storage_idle": (vm["storage_read_mbps"] + vm.get("storage_write_mbps", 0)) < STORAGE_IDLE_THRESHOLD,
    }
    duration_met = vm["idle_hours"] >= IDLE_DURATION_HOURS
    veto_explanation, has_activity_veto = _activity_veto_explanations(vm)

    weights = dict(SIGNAL_WEIGHTS)
    if not has_gpu:
        gpu_w = weights.pop("gpu_idle", 0)
        weights["cpu_idle"] = weights.get("cpu_idle", 0) + gpu_w * 0.6
        weights["network_idle"] = weights.get("network_idle", 0) + gpu_w * 0.4

    score = 0.0
    for signal_name, fired in signals.items():
        if fired is None:
            continue
        if fired:
            score += weights.get(signal_name, 0)
    score = round(min(score, 1.0), 4)

    # Strict: every measured dimension must be "idle" for a waste candidate
    gpu_ok = signals["gpu_idle"] is None or signals["gpu_idle"]
    all_signals_idle = (
        signals["cpu_idle"]
        and signals["ram_idle"]
        and signals["network_idle"]
        and signals["storage_idle"]
        and gpu_ok
    )

    explanation = []
    idle_h = float(vm["idle_hours"])
    if signals["cpu_idle"]:
        explanation.append(
            f"cpu<{CPU_IDLE_THRESHOLD}% for ~{idle_h:.1f}h (actual {vm['cpu_usage_pct']}%)"
        )
    else:
        explanation.append(f"cpu active (>={CPU_IDLE_THRESHOLD}%, actual {vm['cpu_usage_pct']}%)")
    if signals["ram_idle"]:
        explanation.append(f"memory<{RAM_IDLE_THRESHOLD}% (actual {vm['memory_usage_pct']}%)")
    else:
        explanation.append(f"memory not idle (>={RAM_IDLE_THRESHOLD}%, actual {vm['memory_usage_pct']}%)")
    if has_gpu:
        if signals["gpu_idle"]:
            explanation.append(f"gpu<{GPU_IDLE_THRESHOLD}% (actual {vm['gpu_usage_pct']}%)")
        else:
            explanation.append(f"gpu active (actual {vm['gpu_usage_pct']}%)")
    combined_net = vm["network_in_mbps"] + vm.get("network_out_mbps", 0)
    if signals["network_idle"]:
        explanation.append(f"network<{NETWORK_IDLE_THRESHOLD} MB/s (actual {combined_net:.3f} MB/s)")
    else:
        explanation.append(f"network busy (>={NETWORK_IDLE_THRESHOLD} MB/s, actual {combined_net:.3f} MB/s)")
    combined_io = vm["storage_read_mbps"] + vm.get("storage_write_mbps", 0)
    if signals["storage_idle"]:
        explanation.append(f"storage_io<{STORAGE_IDLE_THRESHOLD} MB/s (actual {combined_io:.3f} MB/s)")
    else:
        explanation.append(f"storage_io busy (>={STORAGE_IDLE_THRESHOLD} MB/s, actual {combined_io:.3f} MB/s)")
    if duration_met:
        explanation.append(f"duration>={IDLE_DURATION_HOURS}h (actual {idle_h:.1f}h)")
    else:
        explanation.append(f"duration not met (<{IDLE_DURATION_HOURS}h, actual {idle_h:.1f}h)")

    confirmed_idle = (
        duration_met
        and score >= IDLE_CONFIDENCE_THRESHOLD
        and all_signals_idle
        and not has_activity_veto
    )

    veto_explanation_full = list(veto_explanation)
    if has_activity_veto:
        explanation = veto_explanation + ["— excluded from idle candidates —"] + explanation

    return {
        "signals": signals,
        "duration_met": duration_met,
        "idle_score_gated": score,
        "idle_score": score,
        "explanation": explanation,
        "veto_explanation": veto_explanation,
        "has_activity_veto": has_activity_veto,
        "all_signals_idle": all_signals_idle,
        "confirmed_idle": confirmed_idle,
    }


def detect_idle(vms: list = None) -> list:
    if vms is None:
        with open(VM_DATA_PATH) as f:
            vals = json.load(f)
    else:
        vals = vms

    idle_vms = []
    for vm in vals:
        if is_exempt(vm.get("resource_id", "")):
            continue
        analysis = _compute_idle_score(vm)
        vm["idle_score"] = analysis["idle_score"]
        vm["idle_analysis"] = analysis
        if analysis["confirmed_idle"]:
            idle_vms.append(vm)

    _print_detection_summary(vals, idle_vms)
    return idle_vms


def _print_detection_summary(all_vms: list, idle_vms: list):
    print(f"\n[detect_idle] Scanned {len(all_vms)} VMs -> {len(idle_vms)} waste candidates (strict + veto)\n")

    if not idle_vms:
        print("  No idle candidates. (Check vetoes if VMs looked quiet on CPU only.)\n")
        return

    print(f"  {'Name':<28} {'Type':<12} {'Env':<10} {'CPU%':>5} {'RAM%':>5} "
          f"{'IOBps':>8} {'Score':>6} {'Idle h':>6}")
    print("  " + "─" * 88)

    for v in idle_vms:
        ia = v["idle_analysis"]
        net = v["network_in_mbps"] + v.get("network_out_mbps", 0)
        iob = v["storage_read_mbps"] + v.get("storage_write_mbps", 0)
        print(f"  ~ {v['name']:<26} {v['instance_type']:<12} {v['environment']:<10} "
              f"{v['cpu_usage_pct']:>5.1f} {v['memory_usage_pct']:>5.1f} "
              f"{iob:>8.3f} {ia['idle_score']:>6.2f} {v['idle_hours']:>6.1f}")
        why = [x for x in ia["explanation"] if not x.startswith("VETO:")]
        print(f"      Why: {' | '.join(why[:6])}")

    print()


if __name__ == "__main__":
    from modules.fetch_data import fetch_data

    vms = fetch_data()
    idle = detect_idle(vms)
    print(f"Result: {len(idle)} idle / {len(vms)} total")
