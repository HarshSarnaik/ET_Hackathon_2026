# ☁️ Smart Cloud Cost Saver — Phase 1

> **Rule-based detection + Savings calculator + Twilio notifications + Manual approval**

---

## Architecture

```
Cloud VMs (AWS / Mock)
        ↓
  fetch_data.py          — canonical schema (provider, region, metrics, cost)
        ↓
  detect_idle.py         — multi-signal weighted confidence scoring
   CPU + RAM + GPU + Network + Storage I/O + Duration
        ↓
  cost_calc.py           — waste calc + 30d savings forecast + confidence
        ↓
  decision.py            — policy engine (dev=auto / staging+prod=notify)
        ↓
  ┌─────────────────────────────────────────┐
  │  AUTO_SHUTDOWN (dev, high-confidence)   │ → executor.py → logger.py
  └─────────────────────────────────────────┘
  ┌─────────────────────────────────────────┐
  │  NOTIFY_TWILIO (staging + prod)         │ → twilio_notify.py
  │    WhatsApp / SMS / Voice escalation    │
  │    Approve / Snooze / Exempt / Reject   │
  └─────────────────────────────────────────┘
        ↓
  approval_server.py     — Flask server with dashboard + escalation watchdog
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env
# Fill in TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, phone numbers

# 3. Run in MOCK mode (no AWS or Twilio needed)
python main.py

# 4. Open approval dashboard
open http://localhost:5050/status
```

---

## Config (`config/settings.py`)

| Setting | Default | Description |
|---|---|---|
| `USE_MOCK` | `True` | Simulated VM data vs live AWS |
| `CPU_IDLE_THRESHOLD` | `10%` | Below = CPU idle signal |
| `RAM_IDLE_THRESHOLD` | `20%` | Below = RAM idle signal |
| `IDLE_CONFIDENCE_THRESHOLD` | `0.60` | Minimum score to flag |
| `AUTO_SHUTDOWN_CONFIDENCE_FLOOR` | `0.70` | Minimum to auto-stop dev |
| `ESCALATION_TIMEOUT_MINUTES` | `15` | Re-alert if no ACK |

---

## Idle Detection Logic

A VM is flagged only when **all** of:
1. CPU < threshold  *(required)*
2. Duration >= 2h   *(required)*
3. Weighted score >= 0.60 across CPU + RAM + GPU + Network + Storage

This prevents false positives on:
- Low-CPU but high-memory databases
- Low-CPU but high-network message brokers
- GPU instances between training runs

Each flagged VM gets an `explanation` array:
```json
["cpu<10% for 14.3h (actual: 5.2%)", "memory<20% for 14.3h (actual: 18.4%)", "network<0.5MB/s (actual: 0.003MB/s)"]
```

---

## Decision Policy

| Environment | Action | Requires Approval |
|---|---|---|
| `dev` | AUTO_SHUTDOWN (if conf ≥ 70%) | ❌ |
| `staging` | NOTIFY_TWILIO | ✅ |
| `prod` | NOTIFY_TWILIO (CRITICAL) | ✅ |
| Protected (db/primary) | NOTIFY_TWILIO | ✅ always |

---

## Twilio Alert Flow

```
VM flagged → WhatsApp/SMS to owner team
                ↓
         [15 min ACK timeout]
                ↓ (no response)
         Escalate → manager / on-call
                ↓ (prod CRITICAL)
         Voice call fallback

Action links in every alert:
  ✅ Approve  😴 Snooze 24h  🛡️ Exempt 7d  ❌ Reject
```

---

## Roadmap

| Phase | Status | Scope |
|---|---|---|
| **Phase 1** | ✅ This codebase | Rule-based + Twilio + manual approval |
| Phase 2 | 🔜 | Policy engine + auto-actions + audit trail |
| Phase 3 | 🔜 | ML anomaly detection + savings forecasting + feedback loop |
