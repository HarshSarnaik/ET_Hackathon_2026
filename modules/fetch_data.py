"""
fetch_data.py
=============
MOCK mode  → Generates realistic VM snapshots using the production-ready canonical schema.
REAL mode  → Pulls EC2 + CloudWatch data from AWS via boto3.

Output schema matches the roadmap spec:
  resource_id, provider, account_id, region, resource_type, instance_type,
  environment, owner_team, tags, cpu/memory/gpu/disk/network metrics,
  cost_per_hour_usd, estimated_monthly_cost_usd, last_active_at, ...
"""

import json
import random
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    USE_MOCK, VM_DATA_PATH, INSTANCE_COST, DEFAULT_INSTANCE_COST
)


# ── Mock VM Definitions ───────────────────────────────────────────────────────
MOCK_VM_POOL = [
    {
        "resource_id"   : "i-001a2b3c",
        "name"          : "dev-backend-01",
        "instance_type" : "t2.micro",
        "environment"   : "dev",
        "has_gpu"       : False,
        "owner_team"    : "team-platform",
        "tags"          : {"app": "backend-api", "criticality": "low", "project": "internal"},
        "account_id"    : "111122223333",
        "region"        : "ap-south-1",
    },
    {
        "resource_id"   : "i-002d4e5f",
        "name"          : "dev-frontend-02",
        "instance_type" : "t2.small",
        "environment"   : "dev",
        "has_gpu"       : False,
        "owner_team"    : "team-platform",
        "tags"          : {"app": "frontend", "criticality": "low"},
        "account_id"    : "111122223333",
        "region"        : "ap-south-1",
    },
    {
        "resource_id"   : "i-003g6h7i",
        "name"          : "dev-analytics-03",
        "instance_type" : "t2.medium",
        "environment"   : "dev",
        "has_gpu"       : False,
        "owner_team"    : "team-platform",
        "tags"          : {"app": "analytics", "criticality": "low"},
        "account_id"    : "111122223333",
        "region"        : "ap-south-1",
    },
    {
        "resource_id"   : "i-004j8k9l",
        "name"          : "staging-api-01",
        "instance_type" : "t3.large",
        "environment"   : "staging",
        "has_gpu"       : False,
        "owner_team"    : "team-platform",
        "tags"          : {"app": "api-gateway", "criticality": "medium"},
        "account_id"    : "111122223333",
        "region"        : "ap-south-1",
    },
    {
        "resource_id"   : "i-005m0n1o",
        "name"          : "staging-worker-02",
        "instance_type" : "t3.large",
        "environment"   : "staging",
        "has_gpu"       : False,
        "owner_team"    : "team-platform",
        "tags"          : {"app": "job-worker", "criticality": "medium"},
        "account_id"    : "111122223333",
        "region"        : "ap-south-1",
    },
    {
        "resource_id"   : "i-006p2q3r",
        "name"          : "prod-web-01",
        "instance_type" : "c5.xlarge",
        "environment"   : "prod",
        "has_gpu"       : False,
        "owner_team"    : "team-platform",
        "tags"          : {"app": "web-server", "criticality": "high"},
        "account_id"    : "111122223333",
        "region"        : "ap-south-1",
    },
    {
        "resource_id"   : "i-007s4t5u",
        "name"          : "prod-db-02",
        "instance_type" : "m5.2xlarge",
        "environment"   : "prod",
        "has_gpu"       : False,
        "owner_team"    : "team-platform",
        "tags"          : {"app": "payments-db", "criticality": "critical"},
        "account_id"    : "111122223333",
        "region"        : "ap-south-1",
    },
    {
        "resource_id"   : "i-008v6w7x",
        "name"          : "dev-ml-training-01",
        "instance_type" : "p3.2xlarge",
        "environment"   : "dev",
        "has_gpu"       : True,
        "owner_team"    : "team-ml",
        "tags"          : {"app": "ml-training", "criticality": "medium", "project": "recommendation-v2"},
        "account_id"    : "111122223333",
        "region"        : "ap-south-1",
    },
    {
        "resource_id"   : "i-009y8z9a",
        "name"          : "staging-ml-02",
        "instance_type" : "p3.2xlarge",
        "environment"   : "staging",
        "has_gpu"       : True,
        "owner_team"    : "team-ml",
        "tags"          : {"app": "ml-inference", "criticality": "medium"},
        "account_id"    : "111122223333",
        "region"        : "ap-south-1",
    },
    {
        "resource_id"   : "i-010b0c1d",
        "name"          : "dev-test-runner-04",
        "instance_type" : "t2.medium",
        "environment"   : "dev",
        "has_gpu"       : False,
        "owner_team"    : "team-platform",
        "tags"          : {"app": "ci-runner", "criticality": "low"},
        "account_id"    : "111122223333",
        "region"        : "ap-south-1",
    },
    # Demo: low CPU but hot disk — must NOT be flagged idle (activity veto)
    {
        "resource_id"   : "i-0demo-db-01",
        "name"          : "dev-analytics-sql-05",
        "instance_type" : "t3.large",
        "environment"   : "dev",
        "has_gpu"       : False,
        "owner_team"    : "team-platform",
        "tags": {
            "app": "reporting-sql",
            "criticality": "low",
            "workload_role": "database",
        },
        "account_id"    : "111122223333",
        "region"        : "ap-south-1",
        "_mock_profile" : "db_low_cpu_high_io",
    },
]


def _metrics_db_low_cpu_high_io(vm: dict) -> dict:
    """Low CPU + high disk I/O + long duration — veto should exclude from idle list."""
    now = datetime.datetime.utcnow()
    idle_hours = 48.0
    last_active = (now - datetime.timedelta(hours=idle_hours)).isoformat() + "Z"
    cost_info = INSTANCE_COST.get(vm["instance_type"], DEFAULT_INSTANCE_COST)
    usd_per_hr = cost_info["usd_per_hr"]
    return {
        "provider": "aws",
        "resource_type": "vm",
        "status": "running",
        "timestamp": now.isoformat() + "Z",
        "last_active_at": last_active,
        "cpu_usage_pct": 4.5,
        "memory_usage_pct": 14.0,
        "gpu_usage_pct": 0.0,
        "disk_usage_pct": 55.0,
        "disk_iops": 2800,
        "storage_read_mbps": 0.25,
        "storage_write_mbps": 0.18,
        "network_in_mbps": 0.15,
        "network_out_mbps": 0.08,
        "idle_hours": idle_hours,
        "is_idle_raw": False,
        "cost_per_hour_usd": usd_per_hr,
        "estimated_monthly_cost_usd": round(usd_per_hr * 24 * 30, 2),
        "cost_per_day_inr": cost_info["inr_per_day"],
    }


def _build_metrics(vm: dict) -> dict:
    """
    Generate realistic multi-signal metrics.
    ~60% of VMs are idle — good for demo purposes.
    """
    if vm.get("_mock_profile") == "db_low_cpu_high_io":
        return _metrics_db_low_cpu_high_io(vm)

    is_idle = random.random() < 0.60

    now = datetime.datetime.utcnow()

    if is_idle:
        cpu_pct          = round(random.uniform(1.0, 9.5), 2)
        gpu_pct          = round(random.uniform(0.0, 4.0), 2) if vm["has_gpu"] else 0.0
        memory_pct       = round(random.uniform(5.0, 18.0), 2)
        disk_pct         = round(random.uniform(10.0, 35.0), 2)
        disk_iops        = random.randint(0, 50)
        storage_read     = round(random.uniform(0.0, 0.5), 3)
        storage_write    = round(random.uniform(0.0, 0.2), 3)
        network_in       = round(random.uniform(0.0, 0.1), 3)
        network_out      = round(random.uniform(0.0, 0.05), 3)
        idle_hours       = round(random.uniform(2.0, 72.0), 1)
        last_active_at   = (now - datetime.timedelta(hours=idle_hours)).isoformat() + "Z"
    else:
        cpu_pct          = round(random.uniform(30.0, 95.0), 2)
        gpu_pct          = round(random.uniform(40.0, 98.0), 2) if vm["has_gpu"] else 0.0
        memory_pct       = round(random.uniform(40.0, 90.0), 2)
        disk_pct         = round(random.uniform(30.0, 80.0), 2)
        disk_iops        = random.randint(200, 5000)
        storage_read     = round(random.uniform(5.0, 80.0), 2)
        storage_write    = round(random.uniform(2.0, 40.0), 2)
        network_in       = round(random.uniform(1.0, 50.0), 2)
        network_out      = round(random.uniform(0.5, 30.0), 2)
        idle_hours       = 0.0
        last_active_at   = now.isoformat() + "Z"

    cost_info  = INSTANCE_COST.get(vm["instance_type"], DEFAULT_INSTANCE_COST)
    usd_per_hr = cost_info["usd_per_hr"]

    return {
        # ── Canonical resource fields ──────────────────────────────────
        "provider"                  : "aws",
        "resource_type"             : "vm",
        "status"                    : "running",
        "timestamp"                 : now.isoformat() + "Z",
        "last_active_at"            : last_active_at,

        # ── Performance metrics ────────────────────────────────────────
        "cpu_usage_pct"             : cpu_pct,
        "memory_usage_pct"          : memory_pct,
        "gpu_usage_pct"             : gpu_pct,
        "disk_usage_pct"            : disk_pct,
        "disk_iops"                 : disk_iops,
        "storage_read_mbps"         : storage_read,
        "storage_write_mbps"        : storage_write,
        "network_in_mbps"           : network_in,
        "network_out_mbps"          : network_out,

        # ── Idle state ─────────────────────────────────────────────────
        "idle_hours"                : idle_hours,
        "is_idle_raw"               : is_idle,   # raw pre-detection flag

        # ── Cost ───────────────────────────────────────────────────────
        "cost_per_hour_usd"         : usd_per_hr,
        "estimated_monthly_cost_usd": round(usd_per_hr * 24 * 30, 2),
        "cost_per_day_inr"          : cost_info["inr_per_day"],
    }


def fetch_mock_data() -> list:
    """Generate mock VM snapshots and persist to data/vm_data.json."""
    vms = []
    for vm_def in MOCK_VM_POOL:
        metrics = _build_metrics(vm_def)
        record = {**vm_def, **metrics}
        record.pop("_mock_profile", None)
        vms.append(record)

    os.makedirs(os.path.dirname(VM_DATA_PATH), exist_ok=True)
    with open(VM_DATA_PATH, "w") as f:
        json.dump(vms, f, indent=2, default=str)

    active = sum(1 for v in vms if not v["is_idle_raw"])
    idle   = sum(1 for v in vms if v["is_idle_raw"])
    print(f"[fetch_data] ✅  {len(vms)} VMs generated → {active} active, {idle} potentially idle")
    print(f"[fetch_data] 📁  Saved → {VM_DATA_PATH}")
    return vms


def fetch_real_data() -> list:
    """Pull running EC2 instances + 2-hour CloudWatch averages."""
    try:
        import boto3
        from config.settings import AWS_ACCESS_KEY, AWS_SECRET_KEY, REGION
    except ImportError:
        print("[fetch_data] ❌  boto3 not installed. Run: pip install boto3")
        return []

    ec2 = boto3.client("ec2", region_name=REGION,
                        aws_access_key_id=AWS_ACCESS_KEY,
                        aws_secret_access_key=AWS_SECRET_KEY)
    cw  = boto3.client("cloudwatch", region_name=REGION,
                        aws_access_key_id=AWS_ACCESS_KEY,
                        aws_secret_access_key=AWS_SECRET_KEY)

    end   = datetime.datetime.utcnow()
    start = end - datetime.timedelta(hours=2)

    reservations = ec2.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    )["Reservations"]

    vms = []
    for res in reservations:
        for inst in res["Instances"]:
            iid   = inst["InstanceId"]
            itype = inst["InstanceType"]
            tags  = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
            env   = tags.get("Environment", "dev").lower()

            def _cw_avg(metric):
                pts = cw.get_metric_statistics(
                    Namespace="AWS/EC2", MetricName=metric,
                    Dimensions=[{"Name": "InstanceId", "Value": iid}],
                    StartTime=start, EndTime=end, Period=3600,
                    Statistics=["Average"]
                ).get("Datapoints", [])
                return round(sum(p["Average"] for p in pts) / len(pts), 2) if pts else 0.0

            cpu_pct = _cw_avg("CPUUtilization")
            net_in  = round(_cw_avg("NetworkIn") / 1024 / 1024, 3)
            net_out = round(_cw_avg("NetworkOut") / 1024 / 1024, 3)

            cost_info  = INSTANCE_COST.get(itype, DEFAULT_INSTANCE_COST)
            usd_per_hr = cost_info["usd_per_hr"]
            now        = datetime.datetime.utcnow()

            vms.append({
                "resource_id"               : iid,
                "name"                      : tags.get("Name", iid),
                "instance_type"             : itype,
                "environment"               : env,
                "has_gpu"                   : "p" in itype or "g" in itype,
                "owner_team"                : tags.get("Team", "default"),
                "tags"                      : tags,
                "account_id"                : inst.get("OwnerId", "unknown"),
                "region"                    : REGION,
                "provider"                  : "aws",
                "resource_type"             : "vm",
                "status"                    : "running",
                "timestamp"                 : now.isoformat() + "Z",
                "last_active_at"            : now.isoformat() + "Z",
                "cpu_usage_pct"             : cpu_pct,
                "memory_usage_pct"          : 0.0,   # CloudWatch needs CW agent
                "gpu_usage_pct"             : 0.0,
                "disk_usage_pct"            : 0.0,
                "disk_iops"                 : 0,
                "storage_read_mbps"         : 0.0,
                "storage_write_mbps"        : 0.0,
                "network_in_mbps"           : net_in,
                "network_out_mbps"          : net_out,
                "idle_hours"                : 2.0 if cpu_pct < 10 else 0.0,
                "is_idle_raw"               : cpu_pct < 10,
                "cost_per_hour_usd"         : usd_per_hr,
                "estimated_monthly_cost_usd": round(usd_per_hr * 24 * 30, 2),
                "cost_per_day_inr"          : cost_info["inr_per_day"],
            })

    os.makedirs(os.path.dirname(VM_DATA_PATH), exist_ok=True)
    with open(VM_DATA_PATH, "w") as f:
        json.dump(vms, f, indent=2)

    print(f"[fetch_data] ✅  {len(vms)} live EC2 instances fetched → {VM_DATA_PATH}")
    return vms


def fetch_data() -> list:
    return fetch_mock_data() if USE_MOCK else fetch_real_data()


if __name__ == "__main__":
    vms = fetch_data()
    print(f"\n{'ID':<15} {'Name':<28} {'Type':<12} {'Env':<10} {'CPU%':>5} {'RAM%':>5} {'Idle h':>6}")
    print("─" * 85)
    for v in vms:
        tag = "🔴" if v["is_idle_raw"] else "🟢"
        print(f"{tag} {v['resource_id']:<13} {v['name']:<28} {v['instance_type']:<12} "
              f"{v['environment']:<10} {v['cpu_usage_pct']:>5.1f} {v['memory_usage_pct']:>5.1f} "
              f"{v['idle_hours']:>6.1f}")
