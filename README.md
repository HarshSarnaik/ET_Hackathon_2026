# ☁️ Smart Cloud Cost Saver

**Intelligent Cloud Waste Detection & Automated Cost Optimization System**

An AI-powered solution that detects idle cloud resources using multi-signal analysis and intelligently saves money by auto-shutdown in safe environments or notifying owners in critical ones — with real-time alerts via WhatsApp/SMS and a beautiful dashboard.

---

## ✨ Key Features

### 1. 🎯 Intelligent Idle Detection
- Uses **Multi-Signal Detection** instead of single CPU metric
- Analyzes **CPU, RAM, Network, and GPU** usage simultaneously
- **Duration tracking** (e.g., idle for 2+ hours)
- Calculates **Idle Confidence Score** for high accuracy

### 2. 💰 Real-Time Cost & Savings Forecasting
- Shows exact **waste in ₹ / $** per idle machine per day
- **30-Day Savings Projections**
- **All-Time Savings Tracker** with persistent database

### 3. 🛡️ Smart Policy Engine (Environment-Aware)
- **Dev Environment**: Automatically shuts down high-confidence idle machines
- **Staging / Prod**: Sends notifications instead of auto-shutdown (safety first)

### 4. 📱 Twilio Alert System (WhatsApp & SMS)
- Instant detailed **Waste Alerts** sent to instance owners
- Rich context: "CPU < 5% for 4 hours"
- One-click actions directly from phone:
  - ✅ **Approve** shutdown
  - 😴 **Snooze** for 24 hours
  - 🛡️ **Exempt** for 7 days
  - ❌ **Reject** (improves ML feedback)

### 5. 🤖 Machine Learning Ranking
- Uses **Isolation Forest** algorithm for anomaly detection
- Prioritizes highest-impact and most unusual waste
- Reduces alert fatigue by showing critical items first

### 6. 📊 Interactive Streamlit Dashboard
- Live fleet overview with charts (by environment)
- Pending Approvals section
- Action History with exact savings
- **Built-in Live Simulator** for safe demonstrations

### 7. 🎟️ Automated Jira Integration
- Automatically creates Jira tickets for flagged Production/Staging instances

### 8. 🔄 Continuous Feedback Loop
- Learns from Approve/Reject decisions
- Calculates and displays **Precision Score** over time

---

## 🏗️ Architecture & Workflow

```mermaid
flowchart TD
    A[Cloud Environments] -->|API / Simulator| B(1. Fetch Data)
    B --> C(2. Quality Gate)
   
    C -->|Valid Metrics| D(3. Idle Detection Engine)
    D -->|Flags VMs| E(4. Cost & Savings Calculator)
   
    E --> F(5. ML Ranking\nIsolation Forest)
   
    F --> G{6. Smart Policy Engine}
   
    G -->|Dev & Safe| H[Auto-Shutdown Executor]
    G -->|Staging / Prod| I[Twilio Notification System]
   
    I -.->|WhatsApp / SMS| J((Human Engineer))
    J -.->|Approve / Snooze| K[Approval Webhook Server]
    K --> H
   
    G -->|All Flagged VMs| L[Jira Ticketing Integration]
   
    H --> M[(SQLite Tracking DB)]
    K --> M
   
    M --> N[Streamlit Dashboard / UI]
