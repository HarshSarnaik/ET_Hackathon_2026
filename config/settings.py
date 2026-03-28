# ============================================================
#  Smart Cloud Cost Saver — Phase 1 Configuration
#  Roadmap: Rule-based detection + savings calc + Twilio + approval
# ============================================================

import os
from dotenv import load_dotenv

# Load variables from .env if present
load_dotenv()

# ── Mode ──────────────────────────────────────────────────────────────────────
USE_MOCK = os.getenv("USE_MOCK", "true").lower() == "false"       # True = simulated data, False = live AWS

# ── AWS (only when USE_MOCK = False) ─────────────────────────────────────────
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY", "your_access_key")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_KEY", "your_secret_key")
REGION         = os.getenv("AWS_REGION", "ap-south-1")

# ── Twilio ────────────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID",  "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
TWILIO_AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN",   "your_auth_token")
TWILIO_FROM_NUMBER   = os.getenv("TWILIO_FROM_NUMBER",  "+1XXXXXXXXXX")   # your Twilio number
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM","whatsapp:+14155238886")  # Twilio sandbox

# Owner / on-call contacts
OWNER_CONTACTS = {
    "team-platform": {
        "name"      : "Platform Team",
        "phone"     : os.getenv("OWNER_PHONE",   "+91XXXXXXXXXX"),
        "whatsapp"  : os.getenv("OWNER_WHATSAPP","whatsapp:+91XXXXXXXXXX"),
        "channel"   : "whatsapp",   # "sms" | "whatsapp" | "voice"
    },
    "team-ml": {
        "name"      : "ML Team",
        "phone"     : os.getenv("ML_PHONE",      "+91XXXXXXXXXX"),
        "whatsapp"  : os.getenv("ML_WHATSAPP",   "whatsapp:+91XXXXXXXXXX"),
        "channel"   : "whatsapp",
    },
    "default": {
        "name"      : "On-Call Engineer",
        "phone"     : os.getenv("ONCALL_PHONE",  "+91XXXXXXXXXX"),
        "whatsapp"  : os.getenv("ONCALL_WHATSAPP","whatsapp:+91XXXXXXXXXX"),
        "channel"   : "whatsapp",
    },
}

# Escalation config
ESCALATION_TIMEOUT_MINUTES = 15   # re-alert if no ACK after this

# ── Idle Detection Thresholds (WASTE policy — detection only) ────────────────
CPU_IDLE_THRESHOLD       = 10.0   # % CPU
GPU_IDLE_THRESHOLD       = 5.0    # % GPU
RAM_IDLE_THRESHOLD       = 20.0   # % RAM
NETWORK_IDLE_THRESHOLD   = 0.5    # MB/s combined in+out (below = "idle network")
STORAGE_IDLE_THRESHOLD   = 1.0    # MB/s combined read+write (below = "idle storage IO")
IDLE_DURATION_HOURS      = 2      # must be idle this long to flag

# Activity vetoes: above these = sustained workload — NOT a safe "idle" candidate
# (e.g. DB often has low CPU but high disk I/O)
ACTIVITY_VETO_DISK_IOPS       = 100    # CloudWatch / agent IOPS
ACTIVITY_VETO_STORAGE_MBPS    = 2.0    # combined read+write MB/s
ACTIVITY_VETO_NETWORK_MBPS    = 1.0    # combined in+out MB/s

# ── Confidence / Idle Score Weights ─────────────────────────────────────────
# Each signal contributes to a 0–1 confidence score
SIGNAL_WEIGHTS = {
    "cpu_idle"     : 0.35,
    "ram_idle"     : 0.20,
    "gpu_idle"     : 0.20,
    "network_idle" : 0.15,
    "storage_idle" : 0.10,
}

# Minimum weighted confidence to flag as idle
IDLE_CONFIDENCE_THRESHOLD = 0.60

# ── Cost (USD per hour & INR per day per instance type) ──────────────────────
INSTANCE_COST = {
    "t2.micro"   : {"usd_per_hr": 0.012, "inr_per_day": 150 },
    "t2.small"   : {"usd_per_hr": 0.023, "inr_per_day": 300 },
    "t2.medium"  : {"usd_per_hr": 0.047, "inr_per_day": 600 },
    "t3.large"   : {"usd_per_hr": 0.083, "inr_per_day": 1200},
    "c5.xlarge"  : {"usd_per_hr": 0.170, "inr_per_day": 2400},
    "p3.2xlarge" : {"usd_per_hr": 3.060, "inr_per_day": 8000},
    "m5.2xlarge" : {"usd_per_hr": 0.384, "inr_per_day": 3200},
    "m5.xlarge"  : {"usd_per_hr": 0.192, "inr_per_day": 1600},
}
DEFAULT_INSTANCE_COST = {"usd_per_hr": 0.10, "inr_per_day": 500}

# ── ACTION policy per environment (allowed automation — not detection) ─────
# See config/policies.py — keep "waste" thresholds above separate from this table.
ENVIRONMENT_POLICY = {
    "dev":     {"action": "AUTO_SHUTDOWN",  "requires_approval": False, "severity_floor": "LOW"},
    "staging": {"action": "NOTIFY_TWILIO",  "requires_approval": True,  "severity_floor": "MEDIUM"},
    "prod":    {"action": "NOTIFY_TWILIO",  "requires_approval": True,  "severity_floor": "CRITICAL"},
}

# Global kill-switch: when False, never auto-stop (notify / approve flows only)
AUTO_SHUTDOWN_MASTER_ENABLE = True

# Production: never auto-stop by policy (human approval still possible via dashboard)
ALLOW_AUTO_SHUTDOWN_IN_PROD = False

# After human approves in dashboard, allow stop in prod by default
ALLOW_MANUAL_STOP_IN_PROD = True

# When True, executor never calls cloud APIs (logs only)
EXECUTION_DRY_RUN = False

# Base minimum confidence for auto-shutdown; feedback.py may raise this per team
AUTO_SHUTDOWN_CONFIDENCE_FLOOR_BASE = 0.70
FEEDBACK_WINDOW_SIZE = 40
FEEDBACK_REJECT_RATIO_MAX_BUMP = 0.15

# Protected name keywords — block automation (name tag / matching)
PROTECTED_KEYWORDS = ["db", "database", "primary", "master", "redis", "elasticache", "kafka", "sql"]

# ── Approval Server ──────────────────────────────────────────────────────────
APPROVAL_HOST        = "0.0.0.0"
APPROVAL_PORT        = 5050
APPROVAL_BASE_URL    = os.getenv("APPROVAL_BASE_URL", f"http://localhost:{APPROVAL_PORT}")
APPROVAL_TIMEOUT_SEC = ESCALATION_TIMEOUT_MINUTES * 60

# ── File Paths ────────────────────────────────────────────────────────────────
VM_DATA_PATH      = "data/vm_data.json"
SAVINGS_LOG_PATH  = "logs/savings_log.json"
ACTION_LOG_PATH   = "logs/action_log.json"
ALERT_LOG_PATH    = "logs/alert_log.json"
FEEDBACK_LOG_PATH = "logs/feedback_log.json"
APPROVAL_DB_PATH  = "data/pending_approvals.json"

# ── Phase 2: Observability ───────────────────────────────────────────────────
METRICS_SERVER_PORT = int(os.getenv("METRICS_SERVER_PORT", "8080"))

# ── Phase 2: Policy Engine v2 ───────────────────────────────────────────────
BLAST_RADIUS_LIMIT = int(os.getenv("BLAST_RADIUS_LIMIT", "5"))   # max auto-stops per run
DRY_RUN_MODE       = os.getenv("DRY_RUN_MODE", "false").lower() in ("true", "1", "yes")

# Freeze windows: no auto-shutdown during these times (UTC)
# Format: [{"start": "HH:MM", "end": "HH:MM", "days": [0-6]}]  (0=Mon,6=Sun)
FREEZE_WINDOWS = []   # e.g. [{"start":"09:00","end":"11:00","days":[0,1,2,3,4]}]

# Maintenance windows: prefer notification over auto during these times
MAINTENANCE_WINDOWS = []

# ── Phase 2: Jira Integration ───────────────────────────────────────────────
JIRA_ENABLED     = os.getenv("JIRA_ENABLED", "true").lower() in ("true", "1", "yes")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "CLOUD")
JIRA_BASE_URL    = os.getenv("JIRA_BASE_URL", "https://your-jira.atlassian.net")

# ── Phase 2: Database ───────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "db/cloud_cost_saver.db")
