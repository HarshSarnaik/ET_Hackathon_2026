"""
cost_calc.py
============
Phase 1 Savings Calculator.

For each idle VM, computes:
  - waste already burned (idle_hours × hourly rate)
  - GPU surcharge (GPU instances cost money even idle)
  - projected daily / monthly / annual waste
  - predicted_savings_30d_usd  (roadmap field)
  - decision_confidence        (combined idle_score + cost signal)
  - severity classification    (LOW / MEDIUM / HIGH / CRITICAL)
  - resource efficiency %

All cost fields output in both USD and INR.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import INSTANCE_COST, DEFAULT_INSTANCE_COST

# GPU instances: roughly 40% of on-demand cost is attributed to GPU
GPU_COST_FRACTION = 0.40

# Working days assumption for annual projection
WORKING_DAYS_PER_YEAR = 240


def calculate_cost(vm: dict) -> dict:
    """
    Enriches a VM record with a 'cost_analysis' block.
    """
    itype      = vm.get("instance_type", "t2.micro")
    cost_info  = INSTANCE_COST.get(itype, DEFAULT_INSTANCE_COST)
    usd_per_hr = vm.get("cost_per_hour_usd", cost_info["usd_per_hr"])
    inr_per_day= vm.get("cost_per_day_inr",  cost_info["inr_per_day"])

    inr_per_hr = inr_per_day / 24
    idle_hours = vm.get("idle_hours", 0)
    has_gpu    = vm.get("has_gpu", False)
    env        = vm.get("environment", "prod")
    idle_score = vm.get("idle_score", 0.5)

    # ── Base waste already burned ─────────────────────────────────────────────
    waste_usd = round(idle_hours * usd_per_hr, 4)
    waste_inr = round(idle_hours * inr_per_hr, 2)

    # ── GPU surcharge ─────────────────────────────────────────────────────────
    gpu_surcharge_usd = 0.0
    gpu_surcharge_inr = 0.0
    if has_gpu and vm.get("gpu_usage_pct", 0) < 5:
        gpu_surcharge_usd = round(usd_per_hr * GPU_COST_FRACTION * idle_hours, 4)
        gpu_surcharge_inr = round(inr_per_hr * GPU_COST_FRACTION * idle_hours, 2)

    total_waste_usd = round(waste_usd + gpu_surcharge_usd, 4)
    total_waste_inr = round(waste_inr + gpu_surcharge_inr, 2)

    # ── Forward projections ───────────────────────────────────────────────────
    daily_waste_usd    = round(usd_per_hr * 24, 4)
    daily_waste_inr    = inr_per_day
    monthly_waste_usd  = round(usd_per_hr * 24 * 30, 2)
    monthly_waste_inr  = round(inr_per_day * 30, 2)
    annual_waste_usd   = round(usd_per_hr * 24 * WORKING_DAYS_PER_YEAR, 2)
    annual_waste_inr   = round(inr_per_day * WORKING_DAYS_PER_YEAR, 2)

    # ── Predicted 30-day savings (roadmap field) ──────────────────────────────
    # Assumes we act today; savings = full 30d cost × idle_score confidence
    predicted_savings_30d_usd = round(monthly_waste_usd * idle_score, 2)

    # ── Decision confidence (roadmap field) ───────────────────────────────────
    # Blend idle detection confidence with cost materiality signal
    cost_signal = min(monthly_waste_usd / 200, 1.0)   # normalise: $200/mo = full signal
    decision_confidence = round(idle_score * 0.7 + cost_signal * 0.3, 4)

    # ── Resource efficiency score ─────────────────────────────────────────────
    cpu_eff  = vm["cpu_usage_pct"]    / 100
    ram_eff  = vm["memory_usage_pct"] / 100
    gpu_eff  = vm["gpu_usage_pct"]    / 100 if has_gpu else cpu_eff
    net_eff  = min((vm["network_in_mbps"] + vm.get("network_out_mbps", 0)) / 50, 1.0)
    io_eff   = min((vm["storage_read_mbps"] + vm.get("storage_write_mbps", 0)) / 50, 1.0)
    efficiency_pct = round(((cpu_eff + ram_eff + gpu_eff + net_eff + io_eff) / 5) * 100, 1)

    # ── Severity ──────────────────────────────────────────────────────────────
    severity = _severity(total_waste_inr, env, has_gpu)

    # ── Recommended action ────────────────────────────────────────────────────
    recommended_action = _recommend_action(vm, severity, decision_confidence)

    vm["cost_analysis"] = {
        # Waste already incurred
        "waste_so_far_usd"           : total_waste_usd,
        "waste_so_far_inr"           : total_waste_inr,
        "gpu_surcharge_inr"          : gpu_surcharge_inr,
        "gpu_surcharge_usd"          : gpu_surcharge_usd,
        # Daily projections
        "daily_waste_usd"            : daily_waste_usd,
        "daily_waste_inr"            : daily_waste_inr,
        # Monthly projections
        "monthly_waste_usd"          : monthly_waste_usd,
        "monthly_waste_inr"          : monthly_waste_inr,
        # Annual projections (240 working days)
        "annual_waste_usd"           : annual_waste_usd,
        "annual_waste_inr"           : annual_waste_inr,
        # Roadmap canonical fields
        "predicted_savings_30d_usd"  : predicted_savings_30d_usd,
        "decision_confidence"        : decision_confidence,
        "recommended_action"         : recommended_action,
        "resource_efficiency_pct"    : efficiency_pct,
        "severity"                   : severity,
    }

    # Hoist roadmap top-level fields onto the VM record itself
    vm["predicted_savings_30d_usd"] = predicted_savings_30d_usd
    vm["decision_confidence"]       = decision_confidence
    vm["recommended_action"]        = recommended_action
    vm["anomaly_score"]             = round(idle_score, 4)

    return vm


def _severity(waste_inr: float, env: str, has_gpu: bool) -> str:
    """
    Classify urgency for prioritization & escalation.
    """
    if env == "prod":
        return "CRITICAL"
    if has_gpu and waste_inr > 500:
        return "HIGH"
    if waste_inr > 5000:
        return "HIGH"
    if waste_inr > 1000:
        return "MEDIUM"
    return "LOW"


def _recommend_action(vm: dict, severity: str, confidence: float) -> str:
    """
    Suggest an action based on environment, severity, and confidence.
    """
    env = vm.get("environment", "prod")
    if env == "dev":
        if confidence >= 0.80:
            return "stop_now"
        return "stop_nights_weekends"
    if env == "staging":
        return "stop_nights_weekends"
    return "rightsize"   # prod — never stop, suggest rightsizing


def calculate_all(idle_vms: list) -> list:
    """Run cost calculation on all idle VMs and print summary."""
    enriched = [calculate_cost(vm) for vm in idle_vms]
    _print_cost_summary(enriched)
    return enriched


def _print_cost_summary(vms: list):
    total_waste_inr     = sum(v["cost_analysis"]["waste_so_far_inr"]   for v in vms)
    total_savings_30d   = sum(v["cost_analysis"]["predicted_savings_30d_usd"] for v in vms)
    total_annual_inr    = sum(v["cost_analysis"]["annual_waste_inr"]   for v in vms)

    print(f"\n[cost_calc] 💸  Cost Leakage Analysis\n")
    header = f"  {'Name':<28} {'Type':<12} {'Idle h':>6} {'Waste ₹':>10} {'30d Save $':>10} {'Conf':>6} {'Sev':<10}"
    print(header)
    print("  " + "─" * 90)

    for v in vms:
        ca = v["cost_analysis"]
        print(f"  {v['name']:<28} {v['instance_type']:<12} "
              f"{v['idle_hours']:>6.1f} "
              f"₹{ca['waste_so_far_inr']:>9,.2f} "
              f"${ca['predicted_savings_30d_usd']:>9,.2f} "
              f"{ca['decision_confidence']:>6.2f} "
              f"{ca['severity']:<10}")

    print("  " + "─" * 90)
    print(f"  {'TOTAL WASTE SO FAR':>48}  ₹{total_waste_inr:>10,.2f}")
    print(f"  {'PREDICTED 30-DAY SAVINGS':>48}  ${total_savings_30d:>10,.2f}")
    print(f"  {'ANNUAL RISK (if unchecked)':>48}  ₹{total_annual_inr:>10,.2f}\n")


if __name__ == "__main__":
    from modules.fetch_data  import fetch_data
    from modules.detect_idle import detect_idle
    vms  = fetch_data()
    idle = detect_idle(vms)
    calculate_all(idle)
