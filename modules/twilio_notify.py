"""
twilio_notify.py
================
Twilio-first notification layer (Phase 1).

Implements the roadmap Twilio alert strategy:
  Primary  : WhatsApp or SMS to owner/on-call
  Escalation: If no ACK in 15 min → message manager/on-call group
  Critical prod: Voice call fallback
  Action links : Approve, Snooze 24h, Exempt 7d

In MOCK mode → prints formatted alerts to terminal instead of sending.

Alert payload includes:
  - VM identity, metrics, idle signals
  - Cost waste so far + 30-day savings projection
  - Decision confidence + explanation
  - One-click approve / snooze / exempt / reject links
"""

import json
import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    USE_MOCK,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_FROM_NUMBER,
    TWILIO_WHATSAPP_FROM,
    OWNER_CONTACTS,
    APPROVAL_BASE_URL,
    ALERT_LOG_PATH,
    ESCALATION_TIMEOUT_MINUTES,
)


# ── Rate-limit registry (in-memory for Phase 1, replace with Redis in Phase 2) ─
_alerted_this_run: set = set()


def _get_contact(vm: dict) -> dict:
    """Resolve the right contact for this VM's owner team."""
    team = vm.get("owner_team", "default")
    return OWNER_CONTACTS.get(team, OWNER_CONTACTS["default"])


def _action_urls(vm: dict) -> dict:
    rid = vm["resource_id"]
    return {
        "approve" : f"{APPROVAL_BASE_URL}/approve?id={rid}",
        "snooze"  : f"{APPROVAL_BASE_URL}/snooze?id={rid}&hours=24",
        "exempt"  : f"{APPROVAL_BASE_URL}/exempt?id={rid}&days=7",
        "reject"  : f"{APPROVAL_BASE_URL}/reject?id={rid}",
    }


def _build_sms_body(vm: dict) -> str:
    """Mini SMS that fits within Twilio trial account limits (~320 chars total)."""
    ca  = vm.get("cost_analysis", {})
    dec = vm.get("decision", {})
    sev = ca.get("severity", "LOW")
    rid = vm["resource_id"]
    return (
        f"[{sev}] Idle VM: {vm['name']}\n"
        f"CPU:{vm['cpu_usage_pct']}% Idle:{vm['idle_hours']:.0f}h\n"
        f"Waste: Rs{ca.get('waste_so_far_inr',0):,.0f} | Conf:{dec.get('confidence',0):.0%}\n"
        f"Approve: {APPROVAL_BASE_URL}/approve?id={rid}"
    )


def _build_sms_body_full(vm: dict) -> str:
    """Full SMS body — use on paid Twilio accounts only."""
    ca    = vm.get("cost_analysis", {})
    dec   = vm.get("decision", {})
    urls  = _action_urls(vm)
    sev   = ca.get("severity", "LOW")
    icon  = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(sev, "⚠️")

    return (
        f"{icon} IDLE VM ALERT [{sev}]\n"
        f"VM: {vm['name']} ({vm['instance_type']})\n"
        f"Env: {vm['environment'].upper()} | Idle: {vm['idle_hours']:.1f}h\n"
        f"CPU:{vm['cpu_usage_pct']}% RAM:{vm['memory_usage_pct']}% GPU:{vm['gpu_usage_pct']}%\n"
        f"Waste: Rs{ca.get('waste_so_far_inr',0):,.0f} | 30d: ${ca.get('predicted_savings_30d_usd',0):.2f}\n"
        f"Confidence: {dec.get('confidence',0):.0%}\n"
        f"Approve: {urls['approve']}\n"
        f"Snooze: {urls['snooze']}\n"
        f"Reject: {urls['reject']}"
    )


def _build_whatsapp_body(vm: dict) -> str:
    """Richer WhatsApp message with full breakdown."""
    ca   = vm.get("cost_analysis", {})
    dec  = vm.get("decision", {})
    urls = _action_urls(vm)
    sev  = ca.get("severity", "LOW")
    icon = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(sev, "⚠️")
    
    explanation_list = vm.get("idle_analysis", {}).get("explanation", [])
    if explanation_list:
        why = "\n".join(f"  • {e}" for e in explanation_list)
    else:
        why = "  • CPU and Network inactive"

    return (
        f"{icon} *IDLE VM DETECTED — ACTION REQUIRED* [{sev}]\n\n"
        f"*Instance:* `{vm.get('resource_id', 'Unknown')}` — {vm.get('name', 'Unknown')}\n"
        f"*Status:* CPU: `{vm.get('cpu_usage_pct', 0)}%` | Idle: `{vm.get('idle_hours', 0):.1f}h`\n\n"
        f"💸 *Cost Impact:*\n"
        f"  • Wasted so far: `Rs {ca.get('waste_so_far_inr',0):,.0f}`\n"
        f"  • Daily burn rate: `Rs {ca.get('daily_waste_inr',0):,.0f}/day`\n"
        f"  • 30-day savings: `${ca.get('predicted_savings_30d_usd',0):.2f}`\n\n"
        f"🔍 *Why Flagged:*\n{why}\n\n"
        f"✅ Approve: {urls['approve']}\n"
        f"😴 Snooze: {urls['snooze']}\n"
        f"❌ Reject: {urls['reject']}"
    )


def _build_whatsapp_body_full(vm: dict) -> str:
    """Full WhatsApp body — use on paid Twilio accounts only."""
    ca   = vm.get("cost_analysis", {})
    dec  = vm.get("decision", {})
    urls = _action_urls(vm)
    sev  = ca.get("severity", "LOW")
    icon = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(sev, "⚠️")
    why  = ", ".join(vm.get("idle_analysis", {}).get("explanation", []))

    return (
        f"{icon} *IDLE VM — ACTION REQUIRED* [{sev}]\n\n"
        f"*{vm['name']}* ({vm['instance_type']}, {vm['environment'].upper()})\n"
        f"CPU:{vm['cpu_usage_pct']}% | RAM:{vm['memory_usage_pct']}% | Idle:{vm['idle_hours']:.1f}h\n"
        f"Waste: Rs{ca.get('waste_so_far_inr',0):,.0f} | 30d: ${ca.get('predicted_savings_30d_usd',0):.2f}\n"
        f"Confidence: {dec.get('confidence',0):.0%} | {why}\n\n"
        f"Approve: {urls['approve']}\n"
        f"Snooze: {urls['snooze']}\n"
        f"Reject: {urls['reject']}"
    )


def _send_twilio_message(to: str, body: str, channel: str = "sms") -> bool:
    """Send via Twilio REST API. Returns True on success."""
    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        if channel == "whatsapp":
            msg = client.messages.create(
                body=body, from_=TWILIO_WHATSAPP_FROM, to=to
            )
        else:
            msg = client.messages.create(
                body=body, from_=TWILIO_FROM_NUMBER, to=to
            )
        return msg.sid is not None
    except ImportError:
        print("[twilio] ❌  twilio package not installed. Run: pip install twilio")
        return False
    except Exception as e:
        print(f"[twilio] ❌  API error: {e}")
        return False


def _send_voice_call(to: str, vm: dict) -> bool:
    """
    Trigger a Twilio voice call for CRITICAL prod resources.
    Uses TwiML to speak the alert.
    """
    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        ca      = vm.get("cost_analysis", {})
        twiml   = (
            f"<Response><Say voice='alice'>"
            f"Urgent cloud cost alert. VM {vm['name']} in production is idle "
            f"and burning {int(ca.get('daily_waste_inr', 0))} rupees per day. "
            f"Please review your approval link immediately. "
            f"This is an automated message from the cloud cost saver system."
            f"</Say></Response>"
        )
        call = client.calls.create(
            twiml=twiml,
            from_=TWILIO_FROM_NUMBER,
            to=to
        )
        return call.sid is not None
    except Exception as e:
        print(f"[twilio] ❌  Voice call error: {e}")
        return False


def _mock_print_alert(vm: dict, contact: dict, channel: str, body: str):
    """Print simulated Twilio alert to terminal."""
    ca  = vm.get("cost_analysis", {})
    sev = ca.get("severity", "LOW")
    icon = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(sev, "⚠️")
    ch_icon = {"whatsapp": "💬 WhatsApp", "sms": "📱 SMS", "voice": "📞 Voice"}.get(channel, "📱")

    print(f"\n{'═'*68}")
    print(f"  {ch_icon} TWILIO ALERT → {contact['name']} ({contact.get('phone','N/A')})")
    print(f"  {icon} [{sev}] {vm['name']}  ({vm['instance_type']}  |  {vm['environment'].upper()})")
    print(f"{'─'*68}")
    print(body)
    print(f"{'═'*68}\n")


def send_alert(vm: dict, escalation: bool = False) -> dict:
    """
    Send a Twilio alert for a single VM.
    Returns a result dict with status and metadata.
    """
    rid = vm["resource_id"]

    # Rate-limit: don't spam the same VM twice in one run
    if rid in _alerted_this_run and not escalation:
        print(f"[twilio] ⏭️  Skipping duplicate alert for {vm['name']}")
        return {"sent": False, "reason": "duplicate_suppressed", "resource_id": rid}

    contact = _get_contact(vm)
    sev     = vm.get("cost_analysis", {}).get("severity", "LOW")
    channel = contact.get("channel", "sms")

    # Critical prod → escalate to voice
    if sev == "CRITICAL" and vm.get("environment") == "prod" and not USE_MOCK:
        channel = "voice"

    body = (
        _build_whatsapp_body(vm) if channel == "whatsapp"
        else _build_sms_body(vm)
    )

    result = {
        "resource_id"  : rid,
        "name"         : vm["name"],
        "environment"  : vm.get("environment"),
        "severity"     : sev,
        "channel"      : channel,
        "contact"      : contact["name"],
        "sent_at"      : datetime.datetime.utcnow().isoformat() + "Z",
        "escalation"   : escalation,
        "sent"         : False,
        "urls"         : _action_urls(vm),
    }

    if USE_MOCK:
        _mock_print_alert(vm, contact, channel, body)
        result["sent"] = True
    else:
        if channel == "voice":
            result["sent"] = _send_voice_call(contact["phone"], vm)
        else:
            to = contact.get("whatsapp") if channel == "whatsapp" else contact["phone"]
            result["sent"] = _send_twilio_message(to, body, channel)

    if result["sent"]:
        _alerted_this_run.add(rid)
        _log_alert(result, vm)
        print(f"[twilio] {'📨' if not escalation else '🚨 ESCALATION'}  "
              f"Alert sent → {vm['name']} via {channel} "
              f"({'MOCK' if USE_MOCK else 'LIVE'})")

    return result


def _log_alert(result: dict, vm: dict):
    """Append alert to alert_log.json."""
    os.makedirs(os.path.dirname(ALERT_LOG_PATH), exist_ok=True)
    try:
        with open(ALERT_LOG_PATH) as f:
            log = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        log = []

    log.append({
        **result,
        "vm_snapshot": {
            k: vm[k] for k in
            ("resource_id", "name", "instance_type", "environment",
             "cpu_usage_pct", "memory_usage_pct", "idle_hours",
             "decision_confidence", "predicted_savings_30d_usd")
            if k in vm
        }
    })

    with open(ALERT_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


def notify_all(vms_needing_approval: list) -> list:
    """
    Send Twilio alerts for all VMs requiring approval.
    Returns list of alert results.
    """
    print(f"\n[twilio_notify] 📨  Sending {len(vms_needing_approval)} Twilio alerts...\n")
    results = [send_alert(vm) for vm in vms_needing_approval]
    sent    = sum(1 for r in results if r["sent"])
    print(f"\n[twilio_notify] ✅  {sent}/{len(results)} alerts delivered")
    return results
