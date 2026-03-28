# ============================================================
# Policy separation: WASTE (detection) vs ACTION (automation)
# ============================================================
# WASTE_* answers: "Does this look like wasted / underused capacity?"
# ACTION_* answers: "Are we allowed to stop or notify for this resource?"
# Never mix these in code paths — import both explicitly where needed.

from config.settings import ENVIRONMENT_POLICY
from config import settings as _settings

__all__ = [
    "get_waste_thresholds",
    "get_action_policy_for_env",
    "describe_policy_split",
]


def get_waste_thresholds() -> dict:
    """Thresholds used only by detect_idle / cost analysis."""
    return {
        "cpu_idle_pct": _settings.CPU_IDLE_THRESHOLD,
        "gpu_idle_pct": _settings.GPU_IDLE_THRESHOLD,
        "ram_idle_pct": _settings.RAM_IDLE_THRESHOLD,
        "network_idle_mbps": _settings.NETWORK_IDLE_THRESHOLD,
        "storage_idle_mbps": _settings.STORAGE_IDLE_THRESHOLD,
        "idle_duration_hours": _settings.IDLE_DURATION_HOURS,
        "signal_weights": dict(_settings.SIGNAL_WEIGHTS),
        "idle_confidence_min": _settings.IDLE_CONFIDENCE_THRESHOLD,
        "activity_veto_disk_iops": _settings.ACTIVITY_VETO_DISK_IOPS,
        "activity_veto_storage_mbps": _settings.ACTIVITY_VETO_STORAGE_MBPS,
        "activity_veto_network_mbps": _settings.ACTIVITY_VETO_NETWORK_MBPS,
    }


def get_action_policy_for_env(env: str) -> dict:
    """What automation is permitted for this environment (not whether it looks idle)."""
    key = (env or "prod").lower()
    return dict(ENVIRONMENT_POLICY.get(key, ENVIRONMENT_POLICY["prod"]))


def describe_policy_split() -> str:
    return (
        "WASTE policy: thresholds, signals, vetoes (detect_idle). "
        "ACTION policy: per-env notify/auto/mandatory approval (decision + safety)."
    )
