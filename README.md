# ET_Hackathon_2026

## 🚀 Smart Cloud Cost Saver Agent (MVP)

## 💡 Overview

Modern cloud-based enterprises lose significant money due to idle resources and delayed optimization actions. This project presents a **simple yet powerful AI-driven automation system** that detects idle cloud resources, calculates financial waste, and takes real-time action to prevent cost leakage.

This MVP focuses on **Amazon EC2 instances**, enabling automated cost-saving decisions with minimal infrastructure.

---

## 🎯 Objective

Build an intelligent system that:

* Detects idle virtual machines (VMs)
* Calculates real-time cost leakage
* Sends approval alerts via Slack
* Automatically shuts down idle resources
* Tracks and reports savings

---

## 🧠 Key Idea

> Instead of dashboards that only *show* problems, this system **detects → decides → acts** in real time.

---

## ⚙️ System Architecture

```
AWS EC2 + CloudWatch
        ↓
Python Script (Data Fetching)
        ↓
Idle Detection Logic
        ↓
Cost Calculation Engine
        ↓
Decision Engine
        ↓
Slack Notification (Approval)
        ↓
Action Executor (Shutdown VM)
        ↓
Savings Logger
```

---

## 🔥 Features

* ⚡ Real-time idle VM detection
* 💸 Cost leakage calculation (₹ based)
* 🤖 Automated decision-making logic
* 🔔 Slack-based approval workflow
* 📴 Auto shutdown of unused resources
* 📊 Savings tracking and reporting
* 🧩 Simple, modular architecture

---

## 🧩 Tech Stack

| Component      | Technology                    |
| -------------- | ----------------------------- |
| Cloud Provider | AWS EC2, CloudWatch           |
| Backend        | Python                        |
| Automation     | Cron Jobs                     |
| Notifications  | Slack Webhooks                |
| Data Storage   | JSON / Lightweight DB         |
| Optional AI    | OpenAI API (for explanations) |

---

## 📦 Project Structure

```
cloud-cost-saver/
│
├── data/
│   └── vm_data.json
│
├── modules/
│   ├── fetch_data.py
│   ├── detect_idle.py
│   ├── cost_calc.py
│   ├── decision.py
│   ├── slack_notify.py
│   ├── executor.py
│   └── logger.py
│
├── config/
│   └── settings.py
│
├── main.py
├── requirements.txt
└── README.md
```

---

## 🛠️ How It Works

### 1. Fetch VM Data

* Retrieves EC2 instances and CPU usage via CloudWatch

### 2. Detect Idle Resources

```python
if cpu_usage < 10% for 2 hours:
    idle = True
```

### 3. Calculate Cost Leakage

```python
cost_per_day = 200  # ₹
savings = idle_hours * (cost_per_day / 24)
```

### 4. Decision Logic

```python
if environment == "dev":
    action = "auto_shutdown"
else:
    action = "approval_required"
```

### 5. Slack Notification

* Sends alert with cost impact
* Provides approval button

### 6. Execute Action

* Stops EC2 instance using AWS SDK (boto3)

### 7. Log Savings

* Stores savings data for reporting

---

## 🚀 Setup Instructions

### 🔧 Prerequisites

* AWS account with EC2 access
* IAM credentials configured
* Slack webhook URL
* Python 3.8+

---

### 📥 Installation

```bash
git clone https://github.com/your-repo/cloud-cost-saver.git
cd cloud-cost-saver
pip install -r requirements.txt
```

---

### ⚙️ Configuration

Update `config/settings.py`:

```python
AWS_ACCESS_KEY = "your_key"
AWS_SECRET_KEY = "your_secret"
REGION = "ap-south-1"

SLACK_WEBHOOK_URL = "your_webhook"
```

---

### ▶️ Run the Project

```bash
python main.py
```

---

### ⏱️ Automation (Optional)

Set cron job (every 5 minutes):

```bash
*/5 * * * * python /path/to/main.py
```

---

## 📊 Example Output

### Slack Alert

```
⚠️ Idle VM Detected

Instance: i-12345
CPU Usage: 5%
Estimated Waste: ₹1400/day

Approve shutdown?
```

---

## 💰 Impact

| Metric         | Value                         |
| -------------- | ----------------------------- |
| Detection Time | < 5 minutes                   |
| Automation     | 70%                           |
| Cost Savings   | ₹1,000–₹1,400 per VM/day      |
| Annual Savings | ₹2–3 crore (enterprise scale) |

---

## 🎤 Demo Flow

1. Show running EC2 instance
2. Simulate low CPU usage
3. Trigger system
4. Slack alert appears
5. Click approve
6. VM shuts down instantly ⚡
7. Savings displayed

---

## 🔒 Constraints & Considerations

* Requires secure IAM permissions
* Must avoid shutting down critical workloads
* Approval system for production environments
* Logging for audit compliance

---

## 🌱 Future Enhancements

* Multi-cloud support (Azure, GCP)
* ML-based anomaly detection
* Dynamic pricing optimization
* Dashboard for cost analytics
* Auto-scaling recommendations

---

## 🧠 Innovation Highlight

> This system bridges the gap between **insight and action**, transforming passive dashboards into an **autonomous cost-saving engine**.

---

## 📌 One-Line Pitch

**“An AI-powered agent that detects cloud cost leakage in real time and autonomously shuts it down before money is lost.”**

---

## 👥 Team Roles

* AWS Integration
* Detection & Cost Logic
* Decision Engine / AI
* Notifications & Execution

---

## 📜 License

MIT License

---

## 🙌 Acknowledgements

* AWS Documentation
* Slack API
* OpenAI (optional integration)

---



### Collaborators:
Harsh Sarnaik <br>
Manish Khandait <br>
Prachit Mankar <br>
Rutvik Raut
