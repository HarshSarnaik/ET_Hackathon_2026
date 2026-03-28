"""
modules/metrics_quality.py
==========================
Phase 2 — Metrics Quality Gate.

Validates that each VM has minimum required metrics before entering
the detection pipeline. Drops VMs with stale, missing, or corrupt data.

Returns: (quality_vms, dropped_vms)
"""

import sys
import os
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Minimum required fields — a VM must have at least one of each group
REQUIRED_METRIC_GROUPS = [
    ["cpu_usage", "cpu_usage_pct"],           # CPU
    ["ram_usage", "memory_usage_pct"],         # RAM
]
OPTIONAL_METRICS = ["gpu_usage", "gpu_usage_pct",
                    "network_in_mbps", "network_out_mbps",
                    "storage_read_mbps", "storage_write_mbps"]

# Maximum allowed age of metrics snapshot (hours)
MAX_METRICS_AGE_HOURS = 48


def _has_required_metrics(vm: dict) -> bool:
    """Check that all required metric groups have at least one valid field."""
    metrics = vm.get("metrics") or vm
    for group in REQUIRED_METRIC_GROUPS:
        found = False
        for key in group:
            val = metrics.get(key)
            if val is not None:
                try:
                    float(val)
                    found = True
                    break
                except (TypeError, ValueError):
                    continue
        if not found:
            return False
    return True


def _metrics_are_fresh(vm: dict) -> bool:
    """Check that the metrics snapshot is not stale."""
    last_seen = vm.get("last_seen") or vm.get("metrics_timestamp")
    if not last_seen:
        # If no timestamp, assume fresh (mock data often omits this)
        return True
    try:
        if isinstance(last_seen, str):
            ts = datetime.datetime.fromisoformat(last_seen.rstrip("Z"))
        else:
            ts = last_seen
        age = datetime.datetime.utcnow() - ts
        return age.total_seconds() < MAX_METRICS_AGE_HOURS * 3600
    except Exception:
        return True  # Can't parse → don't drop


def _values_in_range(vm: dict) -> bool:
    """Sanity-check that metric values are within plausible ranges."""
    metrics = vm.get("metrics") or vm
    cpu = metrics.get("cpu_usage") or metrics.get("cpu_usage_pct")
    ram = metrics.get("ram_usage") or metrics.get("memory_usage_pct")
    try:
        if cpu is not None and (float(cpu) < 0 or float(cpu) > 100):
            return False
        if ram is not None and (float(ram) < 0 or float(ram) > 100):
            return False
    except (TypeError, ValueError):
        return False
    return True


def run_quality_gate(vms: list) -> tuple:
    """
    Filter VMs through the metrics quality gate.

    Returns:
        (passed_vms, dropped_vms)
    """
    passed = []
    dropped = []
    reasons = {}

    for vm in vms:
        drop_reasons = []

        if not _has_required_metrics(vm):
            drop_reasons.append("missing_required_metrics")
        if not _metrics_are_fresh(vm):
            drop_reasons.append("stale_metrics")
        if not _values_in_range(vm):
            drop_reasons.append("values_out_of_range")

        if drop_reasons:
            vm["quality_gate"] = {"passed": False, "reasons": drop_reasons}
            dropped.append(vm)
            for r in drop_reasons:
                reasons[r] = reasons.get(r, 0) + 1
        else:
            # Count how many optional metrics are present
            metrics = vm.get("metrics") or vm
            optional_present = sum(
                1 for k in OPTIONAL_METRICS if metrics.get(k) is not None
            )
            vm["quality_gate"] = {
                "passed": True,
                "optional_metrics_count": optional_present,
                "total_optional": len(OPTIONAL_METRICS),
            }
            passed.append(vm)

    # Print summary
    total = len(vms)
    print(f"  [quality] Processed {total} VMs")
    print(f"  [quality] ✅ Passed: {len(passed)}  |  ❌ Dropped: {len(dropped)}")
    if reasons:
        for reason, count in reasons.items():
            print(f"  [quality]    • {reason}: {count}")
    if not dropped:
        print(f"  [quality] All VMs passed quality gate.")

    return passed, dropped
