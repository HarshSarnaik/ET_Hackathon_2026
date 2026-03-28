"""
simulator/live_engine.py
=========================
Live detection + WhatsApp alert loop.

Reads the latest vm_data.json every SCAN_SECONDS, runs idle detection,
and fires a WhatsApp alert for any VM that just became idle (per-session
cooldown prevents duplicate alerts for the same VM).

Appends every alert event to logs/live_events.json.

Run standalone (with simulator already running):
    python -m simulator.live_engine

Or imported and called as a daemon thread from app.py.
"""

import os
import sys
import json
import time
import threading
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv(override=True)

BASE_DIR     = os.path.dirname(os.path.dirname(__file__))
VM_DATA_PATH = os.path.join(BASE_DIR, "data", "vm_data.json")
EVENTS_PATH  = os.path.join(BASE_DIR, "logs", "live_events.json")
FLAG_PATH    = os.path.join(BASE_DIR, "data", "sim_running.flag")

SCAN_SECONDS       = 15     # How often to check for new idle VMs
IDLE_CPU_THRESHOLD = 10.0
IDLE_HOURS_MIN     = 2.0    # Must be idle at least this long to alert

_alerted_this_session: set = set()
_lock    = threading.Lock()
_stop    = threading.Event()


def _now() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _load_vms() -> list:
    try:
        with open(VM_DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _append_event(event: dict):
    os.makedirs(os.path.dirname(EVENTS_PATH), exist_ok=True)
    try:
        with open(EVENTS_PATH, encoding="utf-8") as f:
            events = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        events = []
    events.append(event)
    events = events[-200:]
    with open(EVENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2)


def _build_cost_analysis(vm: dict) -> dict:
    inr_per_day  = vm.get("cost_per_day_inr", 500)
    idle_h       = vm.get("idle_hours", 0)
    waste_so_far = round(inr_per_day * idle_h / 24, 2)
    daily_usd    = vm.get("cost_per_hour_usd", 0.10) * 24
    savings_30d  = round(daily_usd * 30, 2)
    severity     = (
        "CRITICAL" if vm.get("environment") == "prod" and inr_per_day >= 2000
        else "HIGH"   if inr_per_day >= 3000
        else "MEDIUM" if inr_per_day >= 800
        else "LOW"
    )
    return {
        "severity":               severity,
        "waste_so_far_inr":       waste_so_far,
        "daily_waste_inr":        inr_per_day,
        "predicted_savings_30d_usd": savings_30d,
        "annual_waste_inr":       inr_per_day * 365,
    }


def _build_alert_vm(vm: dict) -> dict:
    """Enrich a raw VM dict with cost_analysis, decision, idle_analysis."""
    ca   = _build_cost_analysis(vm)
    conf = min(1.0, 0.60 + (vm.get("idle_hours", 0) / 100))
    reasons = []
    if vm.get("cpu_usage_pct", 100) < IDLE_CPU_THRESHOLD:
        reasons.append(f"CPU {vm['cpu_usage_pct']}% < {IDLE_CPU_THRESHOLD}%")
    if vm.get("memory_usage_pct", 100) < 20:
        reasons.append(f"RAM {vm['memory_usage_pct']}% < 20%")
    net = vm.get("network_in_mbps", 0) + vm.get("network_out_mbps", 0)
    if net < 0.5:
        reasons.append(f"Network {net:.2f} MB/s")
    reasons.append(f"Idle {vm.get('idle_hours', 0):.1f}h")

    return {
        **vm,
        "cost_analysis": ca,
        "decision":      {"confidence": round(conf, 4)},
        "idle_analysis": {"explanation": reasons},
    }


def _send_whatsapp_alert(vm_enriched: dict) -> bool:
    """Send a compact WhatsApp alert. Returns True on success."""
    try:
        from modules.twilio_notify import _build_whatsapp_body, _send_twilio_message
        from config.settings import OWNER_WHATSAPP, OWNER_CONTACTS

        body   = _build_whatsapp_body(vm_enriched)
        team   = vm_enriched.get("owner_team", "default")
        contact = OWNER_CONTACTS.get(team, OWNER_CONTACTS.get("default", {}))
        to_num  = contact.get("whatsapp", OWNER_WHATSAPP if hasattr(OWNER_WHATSAPP, '__str__') else "")
        if not to_num:
            to_num = os.getenv("OWNER_WHATSAPP", "")
        if not to_num.startswith("whatsapp:"):
            to_num = f"whatsapp:{to_num}"

        return _send_twilio_message(to=to_num, body=body, channel="whatsapp")
    except Exception as e:
        print(f"[live_engine] Alert error: {e}")
        return False


def _scan_once():
    """One detection scan: check for newly idle VMs and alert."""
    vms = _load_vms()
    alerted = []

    for vm in vms:
        rid = vm.get("resource_id", "")
        if not vm.get("is_idle_raw"):
            continue
        if vm.get("idle_hours", 0) < IDLE_HOURS_MIN:
            continue
        if rid in _alerted_this_session:
            continue

        vm_enriched = _build_alert_vm(vm)
        print(f"[live_engine] Idle VM detected: {vm['name']} (CPU={vm.get('cpu_usage_pct')}%, idle={vm.get('idle_hours'):.1f}h)")

        sent = _send_whatsapp_alert(vm_enriched)
        ca   = vm_enriched["cost_analysis"]

        event = {
            "ts":          _now(),
            "type":        "ALERT_SENT" if sent else "ALERT_FAILED",
            "resource_id": rid,
            "name":        vm["name"],
            "environment": vm.get("environment", "?"),
            "cpu":         vm.get("cpu_usage_pct", 0),
            "idle_hours":  vm.get("idle_hours", 0),
            "severity":    ca["severity"],
            "savings_30d": ca["predicted_savings_30d_usd"],
        }
        _append_event(event)
        _alerted_this_session.add(rid)
        alerted.append(vm["name"])

        if sent:
            print(f"[live_engine] WhatsApp alert sent for {vm['name']}")
        else:
            print(f"[live_engine] Alert FAILED for {vm['name']}")

    return alerted


def reset_session():
    """Clear the cooldown set — allows re-alerting all VMs."""
    _alerted_this_session.clear()


def run_engine(stop_event: threading.Event = None):
    """Main engine loop. Runs until stop_event is set or FLAG_PATH removed."""
    if stop_event is None:
        stop_event = _stop

    print(f"[live_engine] Started. Scanning every {SCAN_SECONDS}s.")
    try:
        while not stop_event.is_set():
            if not os.path.exists(FLAG_PATH):
                print("[live_engine] Simulator stopped — pausing scans.")
                time.sleep(SCAN_SECONDS)
                continue
            with _lock:
                alerted = _scan_once()
            if alerted:
                print(f"[live_engine] Alerted: {alerted}")
            time.sleep(SCAN_SECONDS)
    except KeyboardInterrupt:
        print("[live_engine] Stopped by user.")


if __name__ == "__main__":
    run_engine()
