"""
simulator/vm_simulator.py
==========================
Real-time VM metric simulator for the Cloud Cost Saver prototype.

Continuously mutates CPU/RAM/Network metrics to simulate VMs drifting
in and out of idle state. Writes fresh data to `data/vm_data.json`
every TICK_SECONDS. Designed to run as a background thread or standalone.

Run standalone:
    python -m simulator.vm_simulator
"""

import json
import os
import sys
import time
import random
import copy
import datetime
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv(override=True)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
VM_DATA_PATH = os.path.join(BASE_DIR, "data", "vm_data.json")
BACKUP_PATH  = os.path.join(BASE_DIR, "data", "vm_data_original.json")
FLAG_PATH    = os.path.join(BASE_DIR, "data", "sim_running.flag")
EVENTS_PATH  = os.path.join(BASE_DIR, "logs", "live_events.json")

TICK_SECONDS   = 5      # How often to update all VM metrics
IDLE_THRESHOLD = 10.0   # CPU % below this = idle signal

# --- VM Template Pool -----------------------------------------------------------
# These are the "base" VM definitions. The simulator spawns and mutates them.
VM_TEMPLATES = [
    {"resource_id": "i-001a2b3c", "name": "dev-backend-01",       "instance_type": "t2.micro",   "environment": "dev",     "owner_team": "team-platform", "has_gpu": False},
    {"resource_id": "i-002d4e5f", "name": "dev-frontend-02",      "instance_type": "t2.small",   "environment": "dev",     "owner_team": "team-platform", "has_gpu": False},
    {"resource_id": "i-003g6h7i", "name": "dev-analytics-03",     "instance_type": "t2.medium",  "environment": "dev",     "owner_team": "team-platform", "has_gpu": False},
    {"resource_id": "i-004j8k9l", "name": "staging-api-01",       "instance_type": "t3.large",   "environment": "staging", "owner_team": "team-platform", "has_gpu": False},
    {"resource_id": "i-005m0n1o", "name": "staging-worker-02",    "instance_type": "t3.large",   "environment": "staging", "owner_team": "team-platform", "has_gpu": False},
    {"resource_id": "i-006p2q3r", "name": "prod-web-01",          "instance_type": "c5.xlarge",  "environment": "prod",    "owner_team": "team-platform", "has_gpu": False},
    {"resource_id": "i-007s4t5u", "name": "prod-db-02",           "instance_type": "m5.2xlarge", "environment": "prod",    "owner_team": "team-platform", "has_gpu": False},
    {"resource_id": "i-008v6w7x", "name": "dev-ml-training-01",   "instance_type": "p3.2xlarge", "environment": "dev",     "owner_team": "team-ml",       "has_gpu": True},
    {"resource_id": "i-009y8z9a", "name": "staging-ml-02",        "instance_type": "p3.2xlarge", "environment": "staging", "owner_team": "team-ml",       "has_gpu": True},
    {"resource_id": "i-010b0c1d", "name": "dev-test-runner-04",   "instance_type": "t2.medium",  "environment": "dev",     "owner_team": "team-platform", "has_gpu": False},
    {"resource_id": "i-0demo-db", "name": "dev-analytics-sql-05", "instance_type": "t3.large",   "environment": "dev",     "owner_team": "team-platform", "has_gpu": False},
]

COST_MAP = {
    "t2.micro":   {"usd_per_hr": 0.012, "inr_per_day": 150},
    "t2.small":   {"usd_per_hr": 0.023, "inr_per_day": 300},
    "t2.medium":  {"usd_per_hr": 0.047, "inr_per_day": 600},
    "t3.large":   {"usd_per_hr": 0.083, "inr_per_day": 1200},
    "c5.xlarge":  {"usd_per_hr": 0.170, "inr_per_day": 2400},
    "p3.2xlarge": {"usd_per_hr": 3.060, "inr_per_day": 8000},
    "m5.2xlarge": {"usd_per_hr": 0.384, "inr_per_day": 3200},
}

_lock    = threading.Lock()
_stop    = threading.Event()
_states  = {}   # resource_id -> current simulated state


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"


def _init_states():
    """Initialise each VM with a random starting state."""
    global _states
    _states = {}
    for t in VM_TEMPLATES:
        idle_start = random.random() < 0.3   # 30% start idle
        if idle_start:
            cpu    = round(random.uniform(1, 8), 2)
            ram    = round(random.uniform(5, 18), 2)
            net_in = round(random.uniform(0.01, 0.09), 3)
            idle_h = round(random.uniform(2, 72), 1)
        else:
            cpu    = round(random.uniform(20, 85), 2)
            ram    = round(random.uniform(30, 90), 2)
            net_in = round(random.uniform(1, 50), 3)
            idle_h = 0.0
        gpu = round(random.uniform(0, 2), 2) if not t["has_gpu"] else round(random.uniform(0, 60), 2)
        _states[t["resource_id"]] = {
            "cpu":    cpu,
            "ram":    ram,
            "net_in": net_in,
            "gpu":    gpu,
            "idle_h": idle_h,
            "is_idle": idle_start,
        }


def _mutate(state: dict, template: dict) -> dict:
    """
    Apply a random walk mutation to one VM's metrics.
    VMs have a small probability of transitioning between active↔idle each tick.
    """
    is_idle = state["is_idle"]

    # Transition probability each tick
    if is_idle and random.random() < 0.08:      # 8% chance idle VM wakes up
        is_idle = False
    elif not is_idle and random.random() < 0.05:  # 5% chance active VM goes idle
        is_idle = True

    if is_idle:
        cpu    = _clamp(state["cpu"]    + random.uniform(-1.5, 1.5),   0.5,  9.9)
        ram    = _clamp(state["ram"]    + random.uniform(-2.0, 2.0),   3.0, 19.0)
        net_in = _clamp(state["net_in"] + random.uniform(-0.02, 0.02), 0.001, 0.45)
        gpu    = _clamp(state["gpu"]    + random.uniform(-0.5, 0.5),   0.0, 4.0) if template["has_gpu"] else 0.0
        idle_h = round(state["idle_h"] + TICK_SECONDS / 3600, 2)
    else:
        cpu    = _clamp(state["cpu"]    + random.uniform(-10, 10), 15.0, 95.0)
        ram    = _clamp(state["ram"]    + random.uniform(-5,   5), 25.0, 92.0)
        net_in = _clamp(state["net_in"] + random.uniform(-5,   5),  0.5, 55.0)
        gpu    = _clamp(state["gpu"]    + random.uniform(-5,   5),  5.0, 85.0) if template["has_gpu"] else 0.0
        idle_h = 0.0

    return {
        "cpu":    round(cpu, 2),
        "ram":    round(ram, 2),
        "net_in": round(net_in, 3),
        "gpu":    round(gpu, 2),
        "idle_h": idle_h,
        "is_idle": is_idle,
    }


def _build_vm_record(template: dict, state: dict) -> dict:
    cost  = COST_MAP.get(template["instance_type"], {"usd_per_hr": 0.10, "inr_per_day": 500})
    net_out = round(state["net_in"] * random.uniform(0.05, 0.2), 3)
    disk_iops = random.randint(1, 60) if state["is_idle"] else random.randint(300, 4000)
    return {
        **template,
        "status":                   "running",
        "account_id":               "111122223333",
        "region":                   "ap-south-1",
        "provider":                 "aws",
        "resource_type":            "vm",
        "timestamp":                _now(),
        "last_active_at":           _now() if not state["is_idle"] else
                                    (datetime.datetime.utcnow() - datetime.timedelta(hours=state["idle_h"])).isoformat() + "Z",
        "tags":                     {"app": template["name"], "criticality": "medium"},
        "cpu_usage_pct":            state["cpu"],
        "memory_usage_pct":         state["ram"],
        "gpu_usage_pct":            state["gpu"],
        "disk_usage_pct":           round(random.uniform(10, 70), 2),
        "disk_iops":                disk_iops,
        "storage_read_mbps":        round(random.uniform(0.05, 0.5), 3) if state["is_idle"] else round(random.uniform(1, 30), 3),
        "storage_write_mbps":       round(random.uniform(0.02, 0.3), 3) if state["is_idle"] else round(random.uniform(0.5, 20), 3),
        "network_in_mbps":          state["net_in"],
        "network_out_mbps":         net_out,
        "idle_hours":               state["idle_h"],
        "is_idle_raw":              state["is_idle"],
        "cost_per_hour_usd":        cost["usd_per_hr"],
        "cost_per_day_inr":         cost["inr_per_day"],
        "estimated_monthly_cost_usd": round(cost["usd_per_hr"] * 720, 2),
    }


def _write_vms(vms: list):
    os.makedirs(os.path.dirname(VM_DATA_PATH), exist_ok=True)
    tmp = VM_DATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(vms, f, indent=2)
    os.replace(tmp, VM_DATA_PATH)


def _append_event(event: dict):
    os.makedirs(os.path.dirname(EVENTS_PATH), exist_ok=True)
    try:
        with open(EVENTS_PATH, encoding="utf-8") as f:
            events = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        events = []
    events.append(event)
    events = events[-200:]   # keep last 200 events
    with open(EVENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2)


def _tick():
    """One simulation tick: mutate all VMs, detect transitions, write file."""
    global _states
    vms    = []
    events = []

    for tmpl in VM_TEMPLATES:
        rid      = tmpl["resource_id"]
        old_state = _states[rid]
        new_state = _mutate(old_state, tmpl)
        _states[rid] = new_state

        # Detect transitions
        if not old_state["is_idle"] and new_state["is_idle"]:
            events.append({
                "ts": _now(), "type": "WENT_IDLE",
                "resource_id": rid, "name": tmpl["name"],
                "environment": tmpl["environment"],
                "cpu": new_state["cpu"],
            })
        elif old_state["is_idle"] and not new_state["is_idle"]:
            events.append({
                "ts": _now(), "type": "WOKE_UP",
                "resource_id": rid, "name": tmpl["name"],
                "environment": tmpl["environment"],
                "cpu": new_state["cpu"],
            })

        vms.append(_build_vm_record(tmpl, new_state))

    _write_vms(vms)
    for e in events:
        _append_event(e)

    return events


def _backup_original():
    """Save original vm_data.json before simulation starts."""
    if os.path.exists(VM_DATA_PATH) and not os.path.exists(BACKUP_PATH):
        import shutil
        shutil.copy2(VM_DATA_PATH, BACKUP_PATH)


def _restore_original():
    """Restore original vm_data.json when simulation stops."""
    if os.path.exists(BACKUP_PATH):
        import shutil
        shutil.copy2(BACKUP_PATH, VM_DATA_PATH)


def run_simulator(stop_event: threading.Event = None):
    """Main simulator loop. Runs until stop_event is set or FLAG_PATH removed."""
    global _states
    if stop_event is None:
        stop_event = _stop

    _backup_original()
    _init_states()

    # Write flag
    os.makedirs(os.path.dirname(FLAG_PATH), exist_ok=True)
    with open(FLAG_PATH, "w") as f:
        f.write(_now())

    print(f"[simulator] Started. Updating every {TICK_SECONDS}s. Ctrl+C to stop.")
    try:
        while not stop_event.is_set():
            if not os.path.exists(FLAG_PATH):
                print("[simulator] Flag removed — stopping.")
                break
            with _lock:
                events = _tick()
            for e in events:
                icon = "🔴" if e["type"] == "WENT_IDLE" else "🟢"
                print(f"[simulator] {icon} {e['name']} {e['type']}  CPU={e['cpu']}%")
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        print("[simulator] Stopped by user.")
    finally:
        if os.path.exists(FLAG_PATH):
            os.remove(FLAG_PATH)
        _restore_original()
        print("[simulator] Original vm_data.json restored.")


def get_current_states() -> dict:
    """Return in-memory state snapshot (for use within the same process)."""
    return copy.deepcopy(_states)


if __name__ == "__main__":
    run_simulator()
