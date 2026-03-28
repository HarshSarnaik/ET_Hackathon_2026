"""
app.py — Streamlit Dashboard for Smart Cloud Cost Saver
========================================================
A fully interactive prototype showcasing idle VM detection,
cost analysis, approval workflows, and savings tracking.

Run:
    pip install streamlit plotly pandas
    streamlit run app.py
"""

import streamlit as st
import pandas as pd
import json
import os
import sys
import sqlite3
import time
import datetime
import plotly.express as px
import plotly.graph_objects as go

# ── Path setup ────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config.settings import (
    VM_DATA_PATH, SAVINGS_LOG_PATH, ALERT_LOG_PATH, ACTION_LOG_PATH,
    INSTANCE_COST, DEFAULT_INSTANCE_COST, IDLE_CONFIDENCE_THRESHOLD,
    CPU_IDLE_THRESHOLD, GPU_IDLE_THRESHOLD, RAM_IDLE_THRESHOLD,
    ENVIRONMENT_POLICY, BLAST_RADIUS_LIMIT, USE_MOCK,
)

DB_PATH = os.path.join(BASE_DIR, "db", "cloud_cost_saver.db")

# ── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Cloud Cost Saver",
    page_icon="☁️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    /* Global */
    .stApp {
        font-family: 'Inter', sans-serif;
    }

    /* Metric cards */
    div[data-testid="stMetric"] {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border: 1px solid rgba(99, 102, 241, 0.3);
        border-radius: 12px;
        padding: 16px 20px;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
    }
    div[data-testid="stMetric"] label {
        color: #a5b4fc !important;
        font-weight: 500 !important;
        font-size: 0.85rem !important;
    }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: #e0e7ff !important;
        font-weight: 700 !important;
    }

    /* Sidebar */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f0f23 0%, #1a1a3e 100%);
    }
    section[data-testid="stSidebar"] .stRadio label {
        color: #c7d2fe !important;
    }

    /* Headers */
    h1, h2, h3 {
        color: #e0e7ff !important;
    }

    /* Severity badges */
    .severity-critical { color: #fff; background: #dc2626; padding: 2px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }
    .severity-high     { color: #fff; background: #ea580c; padding: 2px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }
    .severity-medium   { color: #000; background: #facc15; padding: 2px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }
    .severity-low      { color: #fff; background: #22c55e; padding: 2px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }

    /* Status badges */
    .status-running  { color: #22c55e; font-weight: 600; }
    .status-stopped  { color: #ef4444; font-weight: 600; }
    .status-pending  { color: #f59e0b; font-weight: 600; }

    /* Action buttons */
    .stButton > button {
        border-radius: 8px;
        font-weight: 600;
        transition: all 0.2s ease;
    }
    .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(99, 102, 241, 0.4);
    }

    /* Dataframe styling */
    .stDataFrame {
        border-radius: 12px;
        overflow: hidden;
    }

    /* Divider */
    hr { border-color: rgba(99, 102, 241, 0.2) !important; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  DATA LOADERS
# ══════════════════════════════════════════════════════════════════════════════

def _load_json(path: str) -> list:
    full = os.path.join(BASE_DIR, path) if not os.path.isabs(path) else path
    try:
        with open(full, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def load_vm_data() -> pd.DataFrame:
    vms = _load_json(VM_DATA_PATH)
    if not vms:
        return pd.DataFrame()
    df = pd.DataFrame(vms)
    return df


def load_savings_log() -> list:
    return _load_json(SAVINGS_LOG_PATH)


def load_alert_log() -> list:
    return _load_json(ALERT_LOG_PATH)


def _db_query(query: str, params: tuple = ()) -> list[dict]:
    """Run a read-only query against the SQLite DB."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def load_approvals() -> pd.DataFrame:
    rows = _db_query("SELECT * FROM approvals ORDER BY registered_at DESC")
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def load_runs() -> pd.DataFrame:
    rows = _db_query("SELECT * FROM runs ORDER BY started_at DESC LIMIT 20")
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def load_actions() -> pd.DataFrame:
    rows = _db_query("SELECT * FROM actions ORDER BY executed_at DESC")
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def load_detections() -> pd.DataFrame:
    rows = _db_query("SELECT * FROM detections ORDER BY detected_at DESC")
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def get_db_savings() -> dict:
    rows = _db_query(
        "SELECT COALESCE(SUM(savings_daily_inr),0) AS inr, "
        "COALESCE(SUM(savings_daily_usd),0) AS usd FROM actions WHERE success=1"
    )
    return rows[0] if rows else {"inr": 0, "usd": 0}


def get_db_precision() -> dict:
    rows = _db_query(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN was_correct=1 THEN 1 ELSE 0 END) AS correct FROM feedback"
    )
    if rows and rows[0]["total"]:
        r = rows[0]
        return {"total": r["total"], "correct": r["correct"],
                "precision": round(r["correct"] / r["total"] * 100, 1)}
    return {"total": 0, "correct": 0, "precision": None}


# ══════════════════════════════════════════════════════════════════════════════
#  HELPER: Compute severity from cost
# ══════════════════════════════════════════════════════════════════════════════

def compute_severity(vm: dict) -> str:
    daily_inr = vm.get("cost_per_day_inr", 0)
    env = vm.get("environment", "dev")
    if env == "prod" and daily_inr >= 2000:
        return "CRITICAL"
    if daily_inr >= 3000:
        return "HIGH"
    if daily_inr >= 800:
        return "MEDIUM"
    return "LOW"


def severity_badge(sev: str) -> str:
    return f'<span class="severity-{sev.lower()}">{sev}</span>'


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## ☁️ Cloud Cost Saver")
    st.caption("Smart VM Cost Optimization")
    st.divider()

    page = st.radio(
        "Navigate",
        ["📊 Dashboard", "🖥️ All Resources", "⏳ Pending Approvals",
         "📜 Action History", "🔔 Alert Log", "⚡ Live Monitor", "⚙️ Configuration"],
        label_visibility="collapsed",
    )

    st.divider()
    mode_label = "🟢 MOCK MODE" if USE_MOCK else "🔴 LIVE AWS"
    st.markdown(f"**Mode:** {mode_label}")
    st.caption(f"DB: `{os.path.basename(DB_PATH)}`")
    st.caption(f"Last refresh: {datetime.datetime.now().strftime('%H:%M:%S')}")

    if st.button("🔄 Refresh Data", use_container_width=True):
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

if page == "📊 Dashboard":
    st.markdown("# 📊 Cost Optimization Dashboard")
    st.caption("Real-time overview of cloud resource utilization and waste")

    df = load_vm_data()
    savings = load_savings_log()
    db_sav = get_db_savings()
    alerts = load_alert_log()
    precision = get_db_precision()

    # ── Top metrics row ───────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)

    total_vms = len(df) if not df.empty else 0
    idle_vms = int(df["is_idle_raw"].sum()) if not df.empty and "is_idle_raw" in df.columns else 0
    total_monthly_cost = df["estimated_monthly_cost_usd"].sum() if not df.empty and "estimated_monthly_cost_usd" in df.columns else 0
    total_daily_waste_inr = df.loc[df["is_idle_raw"] == True, "cost_per_day_inr"].sum() if not df.empty and "is_idle_raw" in df.columns else 0
    saved_30d = savings[0].get("total_30d_savings_usd", 0) if savings else 0

    c1.metric("VMs Monitored", f"{total_vms}", delta=f"{idle_vms} idle")
    c2.metric("Monthly Fleet Cost", f"${total_monthly_cost:,.0f}")
    c3.metric("Daily Waste (Idle)", f"₹{total_daily_waste_inr:,.0f}")
    c4.metric("30-Day Savings", f"${saved_30d:,.2f}")
    c5.metric("Detection Precision",
              f"{precision['precision']}%" if precision['precision'] else "N/A",
              delta=f"{precision['total']} decisions")

    st.divider()

    # ── Charts row ────────────────────────────────────────────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("### 🏷️ Cost by Environment")
        if not df.empty:
            env_cost = df.groupby("environment")["estimated_monthly_cost_usd"].sum().reset_index()
            env_cost.columns = ["Environment", "Monthly Cost (USD)"]
            fig = px.pie(
                env_cost, values="Monthly Cost (USD)", names="Environment",
                color="Environment",
                color_discrete_map={"dev": "#22c55e", "staging": "#f59e0b", "prod": "#ef4444"},
                hole=0.45,
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e0e7ff", legend=dict(font=dict(color="#a5b4fc")),
                margin=dict(t=20, b=20, l=20, r=20),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No VM data loaded.")

    with col_right:
        st.markdown("### 📈 Resource Utilization")
        if not df.empty:
            util_data = df[["name", "cpu_usage_pct", "memory_usage_pct", "gpu_usage_pct"]].copy()
            util_data = util_data.melt(id_vars="name", var_name="Metric", value_name="Usage %")
            util_data["Metric"] = util_data["Metric"].map({
                "cpu_usage_pct": "CPU", "memory_usage_pct": "RAM", "gpu_usage_pct": "GPU"
            })
            fig = px.bar(
                util_data, x="name", y="Usage %", color="Metric",
                barmode="group",
                color_discrete_map={"CPU": "#6366f1", "RAM": "#06b6d4", "GPU": "#f43f5e"},
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e0e7ff", xaxis_title="", yaxis_title="Usage %",
                legend=dict(font=dict(color="#a5b4fc")),
                margin=dict(t=20, b=20, l=20, r=20),
                xaxis=dict(tickangle=-45),
            )
            fig.add_hline(y=CPU_IDLE_THRESHOLD, line_dash="dash",
                          line_color="#ef4444", annotation_text="Idle Threshold")
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ── Idle VMs table ────────────────────────────────────────────────────────
    st.markdown("### 🔴 Idle Resources Detected")
    if not df.empty:
        idle_df = df[df["is_idle_raw"] == True].copy()
        if not idle_df.empty:
            idle_df["Severity"] = idle_df.apply(lambda r: compute_severity(r.to_dict()), axis=1)
            idle_df["Waste/Day (₹)"] = idle_df["cost_per_day_inr"]
            idle_df["30d Savings ($)"] = idle_df["estimated_monthly_cost_usd"]
            display_cols = ["name", "resource_id", "instance_type", "environment",
                            "cpu_usage_pct", "memory_usage_pct", "idle_hours",
                            "Severity", "Waste/Day (₹)", "30d Savings ($)"]
            show_df = idle_df[display_cols].rename(columns={
                "name": "VM Name", "resource_id": "Resource ID",
                "instance_type": "Type", "environment": "Env",
                "cpu_usage_pct": "CPU %", "memory_usage_pct": "RAM %",
                "idle_hours": "Idle Hours",
            })
            st.dataframe(
                show_df.style
                    .background_gradient(subset=["Idle Hours"], cmap="Reds")
                    .background_gradient(subset=["Waste/Day (₹)"], cmap="OrRd")
                    .format({"CPU %": "{:.1f}", "RAM %": "{:.1f}",
                             "Idle Hours": "{:.1f}h", "Waste/Day (₹)": "₹{:,.0f}",
                             "30d Savings ($)": "${:,.2f}"}),
                use_container_width=True, hide_index=True,
            )
        else:
            st.success("✅ No idle VMs detected — all resources are healthy!")
    else:
        st.info("No VM data available. Run the pipeline first: `python main.py`")


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: ALL RESOURCES
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🖥️ All Resources":
    st.markdown("# 🖥️ All Monitored Resources")
    df = load_vm_data()

    if df.empty:
        st.warning("No VM data found. Run `python main.py` first.")
    else:
        # Filters
        fc1, fc2, fc3 = st.columns(3)
        envs = ["All"] + sorted(df["environment"].unique().tolist())
        teams = ["All"] + sorted(df["owner_team"].unique().tolist())
        status_opts = ["All", "Idle", "Active"]

        sel_env = fc1.selectbox("Environment", envs)
        sel_team = fc2.selectbox("Owner Team", teams)
        sel_status = fc3.selectbox("Status", status_opts)

        filtered = df.copy()
        if sel_env != "All":
            filtered = filtered[filtered["environment"] == sel_env]
        if sel_team != "All":
            filtered = filtered[filtered["owner_team"] == sel_team]
        if sel_status == "Idle":
            filtered = filtered[filtered["is_idle_raw"] == True]
        elif sel_status == "Active":
            filtered = filtered[filtered["is_idle_raw"] == False]

        st.caption(f"Showing {len(filtered)} of {len(df)} resources")

        display_cols = ["name", "resource_id", "instance_type", "environment",
                        "owner_team", "status", "cpu_usage_pct", "memory_usage_pct",
                        "gpu_usage_pct", "idle_hours", "cost_per_day_inr",
                        "estimated_monthly_cost_usd", "is_idle_raw"]
        show = filtered[display_cols].rename(columns={
            "name": "VM Name", "resource_id": "ID", "instance_type": "Type",
            "environment": "Env", "owner_team": "Team", "status": "Status",
            "cpu_usage_pct": "CPU %", "memory_usage_pct": "RAM %",
            "gpu_usage_pct": "GPU %", "idle_hours": "Idle Hrs",
            "cost_per_day_inr": "₹/Day", "estimated_monthly_cost_usd": "$/Month",
            "is_idle_raw": "Idle?",
        })

        st.dataframe(
            show.style.format({
                "CPU %": "{:.1f}", "RAM %": "{:.1f}", "GPU %": "{:.1f}",
                "Idle Hrs": "{:.1f}", "₹/Day": "₹{:,.0f}", "$/Month": "${:,.2f}",
            }),
            use_container_width=True, hide_index=True, height=500,
        )

        # Expandable detail cards
        st.divider()
        st.markdown("### 🔍 VM Detail Inspector")
        vm_names = filtered["name"].tolist()
        selected_vm = st.selectbox("Select a VM to inspect", vm_names)

        if selected_vm:
            vm_row = filtered[filtered["name"] == selected_vm].iloc[0].to_dict()
            dc1, dc2 = st.columns(2)

            with dc1:
                st.markdown(f"**{vm_row['name']}** (`{vm_row['resource_id']}`)")
                st.markdown(f"- **Type:** {vm_row['instance_type']}")
                st.markdown(f"- **Environment:** {vm_row['environment'].upper()}")
                st.markdown(f"- **Team:** {vm_row['owner_team']}")
                st.markdown(f"- **Region:** {vm_row.get('region', 'ap-south-1')}")
                st.markdown(f"- **Status:** {'🟢 Running' if vm_row['status'] == 'running' else '🔴 Stopped'}")
                st.markdown(f"- **Idle:** {'⚠️ YES ({:.1f}h)'.format(vm_row['idle_hours']) if vm_row['is_idle_raw'] else '✅ No'}")

            with dc2:
                # Mini gauge chart
                fig = go.Figure()
                for metric, val, color in [
                    ("CPU", vm_row["cpu_usage_pct"], "#6366f1"),
                    ("RAM", vm_row["memory_usage_pct"], "#06b6d4"),
                    ("GPU", vm_row["gpu_usage_pct"], "#f43f5e"),
                ]:
                    fig.add_trace(go.Bar(name=metric, x=[metric], y=[val],
                                         marker_color=color, text=f"{val:.1f}%",
                                         textposition="outside"))
                fig.update_layout(
                    height=250, showlegend=False,
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#e0e7ff", yaxis=dict(range=[0, 100], title="Usage %"),
                    margin=dict(t=20, b=20, l=40, r=20),
                )
                fig.add_hline(y=CPU_IDLE_THRESHOLD, line_dash="dash", line_color="#ef4444")
                st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: PENDING APPROVALS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "⏳ Pending Approvals":
    st.markdown("# ⏳ Pending Approvals")
    st.caption("VMs awaiting human review before action can be taken")

    approvals_df = load_approvals()
    alerts = load_alert_log()

    if approvals_df.empty and not alerts:
        st.info("No pending approvals. All clear! 🎉")
    else:
        # Use alerts log data to build approval cards
        items = alerts if alerts else []

        if not approvals_df.empty:
            pending = approvals_df[approvals_df["status"] == "PENDING"]
            st.metric("Pending Approvals", len(pending))
            st.divider()

        for i, alert in enumerate(items):
            snap = alert.get("vm_snapshot", {})
            sev = alert.get("severity", "LOW")
            env = alert.get("environment", "?")
            sev_colors = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}

            with st.container():
                st.markdown(f"### {sev_colors.get(sev, '⚠️')} {alert.get('name', 'Unknown')} — `{alert.get('resource_id')}`")

                ic1, ic2, ic3, ic4 = st.columns(4)
                ic1.markdown(f"**Severity:** {sev}")
                ic2.markdown(f"**Env:** {env.upper()}")
                ic3.markdown(f"**Type:** {snap.get('instance_type', '?')}")
                ic4.markdown(f"**Idle:** {snap.get('idle_hours', 0):.1f}h")

                ic5, ic6, ic7, ic8 = st.columns(4)
                ic5.markdown(f"**CPU:** {snap.get('cpu_usage_pct', 0):.1f}%")
                ic6.markdown(f"**RAM:** {snap.get('memory_usage_pct', 0):.1f}%")
                ic7.markdown(f"**Confidence:** {snap.get('decision_confidence', 0):.0%}")
                ic8.markdown(f"**30d Savings:** ${snap.get('predicted_savings_30d_usd', 0):,.2f}")

                bc1, bc2, bc3, bc4 = st.columns(4)

                if bc1.button("✅ Approve", key=f"approve_{i}", type="primary",
                              use_container_width=True):
                    st.success(f"✅ Approved shutdown for **{alert.get('name')}**")

                if bc2.button("😴 Snooze 24h", key=f"snooze_{i}",
                              use_container_width=True):
                    st.warning(f"😴 Snoozed **{alert.get('name')}** for 24 hours")

                if bc3.button("🛡️ Exempt 7d", key=f"exempt_{i}",
                              use_container_width=True):
                    st.info(f"🛡️ Exempted **{alert.get('name')}** for 7 days")

                if bc4.button("❌ Reject", key=f"reject_{i}",
                              use_container_width=True):
                    st.error(f"❌ Rejected shutdown for **{alert.get('name')}**")

                st.divider()


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: ACTION HISTORY
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📜 Action History":
    st.markdown("# 📜 Action & Savings History")

    savings_log = load_savings_log()
    actions_df = load_actions()

    if not savings_log and actions_df.empty:
        st.info("No actions recorded yet. Run the pipeline first.")
    else:
        # Summary from savings_log
        if savings_log:
            latest = savings_log[-1]
            sc1, sc2, sc3, sc4 = st.columns(4)
            sc1.metric("VMs Stopped", latest.get("vms_stopped", 0))
            sc2.metric("Daily Savings", f"₹{latest.get('total_saved_daily_inr', 0):,.0f}")
            sc3.metric("30-Day Projection", f"${latest.get('total_30d_savings_usd', 0):,.2f}")
            sc4.metric("Waste Recovered", f"₹{latest.get('waste_recovered_inr', 0):,.0f}")

            st.divider()

            # Details table
            details = latest.get("details", [])
            if details:
                st.markdown("### 📋 Shutdown Details")
                det_df = pd.DataFrame(details)
                show_cols = ["instance_name", "instance_type", "environment",
                             "action", "savings_daily_inr", "savings_daily_usd",
                             "predicted_savings_30d_usd", "decision_confidence"]
                if all(c in det_df.columns for c in show_cols):
                    show = det_df[show_cols].rename(columns={
                        "instance_name": "VM", "instance_type": "Type",
                        "environment": "Env", "action": "Action",
                        "savings_daily_inr": "₹/Day", "savings_daily_usd": "$/Day",
                        "predicted_savings_30d_usd": "30d Savings ($)",
                        "decision_confidence": "Confidence",
                    })
                    st.dataframe(
                        show.style.format({
                            "₹/Day": "₹{:,.0f}", "$/Day": "${:.3f}",
                            "30d Savings ($)": "${:,.2f}", "Confidence": "{:.0%}",
                        }),
                        use_container_width=True, hide_index=True,
                    )

                # Savings bar chart
                st.markdown("### 💰 Savings Breakdown by VM")
                fig = px.bar(
                    det_df, x="instance_name", y="savings_daily_inr",
                    color="environment",
                    color_discrete_map={"dev": "#22c55e", "staging": "#f59e0b", "prod": "#ef4444"},
                    text="savings_daily_inr",
                )
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#e0e7ff", xaxis_title="", yaxis_title="Daily Savings (₹)",
                    margin=dict(t=20, b=20, l=20, r=20),
                )
                fig.update_traces(texttemplate="₹%{text:,.0f}", textposition="outside")
                st.plotly_chart(fig, use_container_width=True)

        # DB actions
        if not actions_df.empty:
            st.divider()
            st.markdown("### 🗄️ Database Action Records")
            st.dataframe(actions_df, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: ALERT LOG
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🔔 Alert Log":
    st.markdown("# 🔔 Twilio Alert Log")
    
    st.markdown("### 🧪 Test Live Twilio Alert")
    st.caption("Send a real test alert to verify your Twilio credentials. (Ensure `pip install twilio` is installed)")
    
    with st.container(border=True):
        tc1, tc2 = st.columns([2, 2])
        test_contact = tc1.text_input("Send to (E.164 format, e.g. +14155551234)", value="")
        test_channel = tc2.selectbox("Channel", ["whatsapp", "sms"])
        
        if st.button("Send Test Message 🚀", type="primary"):
            if not test_contact:
                st.error("Please enter a valid phone number.")
            else:
                from modules.twilio_notify import _send_twilio_message, _build_whatsapp_body, _build_sms_body
                
                # Fetch a sample VM to make the test look like a real alert
                sample_vm = None
                df = load_vm_data()
                if not df.empty and "is_idle_raw" in df.columns:
                    idle_vms = df[df["is_idle_raw"] == True]
                    if not idle_vms.empty:
                        sample_vm = idle_vms.iloc[0].to_dict()
                        # Ensure 'decision' and 'cost_analysis' exist for the builder functions
                        if "cost_analysis" not in sample_vm:
                            sample_vm["cost_analysis"] = {"severity": "HIGH", "waste_so_far_inr": 1200, "daily_waste_inr": 300, "predicted_savings_30d_usd": 16.50, "annual_waste_inr": 108000}
                        if "decision" not in sample_vm:
                            sample_vm["decision"] = {"confidence": 0.95}
                        if "idle_analysis" not in sample_vm:
                            sample_vm["idle_analysis"] = {"explanation": ["cpu<10% for 40 hours", "network is virtually zero"]}
                            
                # Fallback to a hardcoded dummy VM if no real ones exist
                if not sample_vm:
                    sample_vm = {
                        "resource_id": "i-0dummytest", "name": "test-idle-worker-01",
                        "instance_type": "t3.large", "environment": "staging",
                        "cpu_usage_pct": 2.1, "memory_usage_pct": 14.5, "gpu_usage_pct": 0,
                        "network_in_mbps": 0.05, "network_out_mbps": 0.0, "idle_hours": 48.5,
                        "cost_analysis": {"severity": "MEDIUM", "waste_so_far_inr": 2400, "daily_waste_inr": 1200, "predicted_savings_30d_usd": 59.76, "annual_waste_inr": 438000},
                        "decision": {"confidence": 0.88},
                        "idle_analysis": {"explanation": ["CPU flatlined below 3%", "Network < 0.1 MB/s"]}
                    }

                body = _build_whatsapp_body(sample_vm) if test_channel == "whatsapp" else _build_sms_body(sample_vm)
                to_num = f"whatsapp:{test_contact}" if test_channel == "whatsapp" and not test_contact.startswith("whatsapp:") else test_contact

                if USE_MOCK:
                    st.warning("USE_MOCK is True — force-sending live anyway for this test...")

                with st.spinner(f"Sending real alert template via {test_channel}..."):
                    try:
                        from twilio.rest import Client
                        from config.settings import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, TWILIO_WHATSAPP_FROM
                        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                        if test_channel == "whatsapp":
                            msg = client.messages.create(body=body, from_=TWILIO_WHATSAPP_FROM, to=to_num)
                        else:
                            msg = client.messages.create(body=body, from_=TWILIO_FROM_NUMBER, to=to_num)
                        st.success(f"Sent! SID: `{msg.sid}` | Status: `{msg.status}`")
                        st.markdown("**Message preview:**")
                        st.code(body, language="text")
                    except Exception as e:
                        st.error(f"Twilio Error: {e}")
                        st.markdown("**What you tried to send:**")
                        st.code(body, language="text")

    st.divider()

    alerts = load_alert_log()

    if not alerts:
        st.info("No alerts sent yet.")
    else:
        st.metric("Total Alerts Sent", len(alerts))
        st.divider()

        alert_df = pd.DataFrame(alerts)
        display_cols = ["name", "resource_id", "environment", "severity",
                        "channel", "contact", "sent", "sent_at"]
        available = [c for c in display_cols if c in alert_df.columns]
        show = alert_df[available].rename(columns={
            "name": "VM Name", "resource_id": "ID", "environment": "Env",
            "severity": "Severity", "channel": "Channel", "contact": "Contact",
            "sent": "Delivered?", "sent_at": "Sent At",
        })
        st.dataframe(show, use_container_width=True, hide_index=True)

        # Channel distribution
        if "channel" in alert_df.columns:
            st.markdown("### 📊 Alerts by Channel")
            ch_counts = alert_df["channel"].value_counts().reset_index()
            ch_counts.columns = ["Channel", "Count"]
            fig = px.pie(ch_counts, values="Count", names="Channel", hole=0.4,
                         color_discrete_sequence=["#6366f1", "#06b6d4", "#f43f5e"])
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e0e7ff", margin=dict(t=20, b=20, l=20, r=20),
            )
            st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: LIVE MONITOR
# ══════════════════════════════════════════════════════════════════════════════

elif page == "⚡ Live Monitor":
    import threading
    st.markdown("# ⚡ Live Monitor")
    st.caption("Real-time VM idle detection with automatic WhatsApp alerts")

    FLAG_PATH   = os.path.join(BASE_DIR, "data", "sim_running.flag")
    EVENTS_PATH = os.path.join(BASE_DIR, "logs", "live_events.json")
    sim_running = os.path.exists(FLAG_PATH)

    # ── Control Panel ─────────────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("### 🎛️ Simulation Control")
        cp1, cp2, cp3 = st.columns(3)
        cp1.metric("Simulator Status", "RUNNING" if sim_running else "STOPPED",
                   delta="Live" if sim_running else "Idle")

        if not sim_running:
            if cp2.button("▶️ Start Simulation", type="primary", use_container_width=True):
                # Launch simulator + engine as daemon threads
                from simulator.vm_simulator import run_simulator
                from simulator.live_engine  import run_engine
                stop_ev = threading.Event()
                st.session_state["sim_stop"] = stop_ev
                threading.Thread(target=run_simulator, args=(stop_ev,), daemon=True).start()
                threading.Thread(target=run_engine,    args=(stop_ev,), daemon=True).start()
                st.success("Simulation started! VM metrics will update every 5s.")
                time.sleep(1)
                st.rerun()
        else:
            if cp2.button("⏹️ Stop Simulation", use_container_width=True):
                # Remove flag — simulator thread will self-stop
                if os.path.exists(FLAG_PATH):
                    os.remove(FLAG_PATH)
                if "sim_stop" in st.session_state:
                    st.session_state["sim_stop"].set()
                st.warning("Stopping simulation...")
                time.sleep(1)
                st.rerun()

        if cp3.button("🔄 Refresh Now", use_container_width=True):
            st.rerun()

    st.divider()

    # ── Auto Refresh Toggle ───────────────────────────────────────────────────
    auto_refresh = st.toggle("Auto-refresh every 5s", value=True)

    # ── Live VM Status Grid ───────────────────────────────────────────────────
    st.markdown("### 🖥️ Live VM Status")
    df = load_vm_data()

    if df.empty:
        st.info("No VM data. Click ▶️ Start Simulation above.")
    else:
        # Display 3 VMs per row
        vms_list = df.to_dict(orient="records")
        cols_per_row = 3
        for row_idx in range(0, len(vms_list), cols_per_row):
            row_vms = vms_list[row_idx:row_idx + cols_per_row]
            cols = st.columns(cols_per_row)
            for col, vm in zip(cols, row_vms):
                cpu   = vm.get("cpu_usage_pct", 0)
                ram   = vm.get("memory_usage_pct", 0)
                idle  = vm.get("is_idle_raw", False)
                idle_h = vm.get("idle_hours", 0)
                env   = vm.get("environment", "")

                status_icon  = "🔴 IDLE"   if idle else "🟢 ACTIVE"
                border_color = "#ef4444"  if idle else "#22c55e"
                env_color    = {"prod": "#ef4444", "staging": "#f59e0b", "dev": "#22c55e"}.get(env, "#6366f1")

                with col:
                    st.markdown(f"""
<div style="border:2px solid {border_color}; border-radius:12px; padding:12px 14px;
background:linear-gradient(135deg,#1a1a2e,#16213e); margin-bottom:8px;">
  <div style="display:flex; justify-content:space-between; align-items:center;">
    <b style="color:#e0e7ff; font-size:0.85rem;">{vm['name']}</b>
    <span style="color:{border_color}; font-size:0.75rem; font-weight:700;">{status_icon}</span>
  </div>
  <div style="color:#a5b4fc; font-size:0.72rem; margin:2px 0;">
    {vm['instance_type']} &nbsp;|&nbsp; <span style="color:{env_color};">{env.upper()}</span>
  </div>
  <div style="margin-top:8px; display:flex; gap:12px;">
    <div style="text-align:center;">
      <div style="color:#a5b4fc; font-size:0.68rem;">CPU</div>
      <div style="color:{'#ef4444' if cpu < 10 else '#22c55e'}; font-weight:700; font-size:1rem;">{cpu:.1f}%</div>
    </div>
    <div style="text-align:center;">
      <div style="color:#a5b4fc; font-size:0.68rem;">RAM</div>
      <div style="color:#e0e7ff; font-weight:700; font-size:1rem;">{ram:.1f}%</div>
    </div>
    <div style="text-align:center;">
      <div style="color:#a5b4fc; font-size:0.68rem;">Idle Hrs</div>
      <div style="color:{'#f59e0b' if idle_h > 0 else '#6b7280'}; font-weight:700; font-size:1rem;">{idle_h:.1f}h</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

    st.divider()

    # ── Live Events Feed ──────────────────────────────────────────────────────
    st.markdown("### 📡 Live Event Feed")
    try:
        with open(EVENTS_PATH, encoding="utf-8") as f:
            events = json.load(f)
        events = list(reversed(events[-30:]))   # newest first

        if not events:
            st.info("No events yet. Events will appear here once the simulation is running.")
        else:
            for ev in events:
                ev_type = ev.get("type", "")
                color   = {"WENT_IDLE": "#ef4444", "WOKE_UP": "#22c55e",
                           "ALERT_SENT": "#f59e0b", "ALERT_FAILED": "#6b7280"}.get(ev_type, "#a5b4fc")
                icon    = {"WENT_IDLE": "🔴", "WOKE_UP": "🟢",
                           "ALERT_SENT": "📲", "ALERT_FAILED": "⚠️"}.get(ev_type, "📌")
                ts      = ev.get("ts", "")[:19].replace("T", " ")

                detail = ""
                if ev_type in ("WENT_IDLE", "WOKE_UP"):
                    detail = f"CPU: {ev.get('cpu', 0):.1f}%"
                elif ev_type == "ALERT_SENT":
                    detail = f"Idle: {ev.get('idle_hours', 0):.1f}h | Sev: {ev.get('severity', '?')} | WhatsApp sent!"
                elif ev_type == "ALERT_FAILED":
                    detail = "WhatsApp send failed — check terminal"

                st.markdown(f"""
<div style="display:flex; gap:10px; align-items:center; padding:6px 10px;
border-left:3px solid {color}; margin-bottom:4px;
background:rgba(255,255,255,0.02); border-radius:0 8px 8px 0;">
  <span style="font-size:1rem;">{icon}</span>
  <div>
    <span style="color:{color}; font-weight:600; font-size:0.82rem;">{ev_type.replace('_',' ')}</span>
    &nbsp;<span style="color:#a5b4fc; font-size:0.82rem;">— {ev.get('name','?')}</span>
    &nbsp;<span style="color:#6b7280; font-size:0.75rem;">({ev.get('environment','?').upper()})</span><br/>
    <span style="color:#6b7280; font-size:0.72rem;">{ts} UTC &nbsp;|&nbsp; {detail}</span>
  </div>
</div>
""", unsafe_allow_html=True)
    except (FileNotFoundError, json.JSONDecodeError):
        st.info("No events yet.")

    # Auto-refresh
    if auto_refresh and sim_running:
        time.sleep(5)
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE: CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

elif page == "⚙️ Configuration":
    st.markdown("# ⚙️ System Configuration")
    st.caption("Current active settings from `config/settings.py`")

    cc1, cc2 = st.columns(2)

    with cc1:
        st.markdown("### 🎯 Idle Detection Thresholds")
        st.markdown(f"""
| Parameter               | Value      |
|-------------------------|------------|
| CPU Idle Threshold      | `{CPU_IDLE_THRESHOLD}%`   |
| GPU Idle Threshold      | `{GPU_IDLE_THRESHOLD}%`   |
| RAM Idle Threshold      | `{RAM_IDLE_THRESHOLD}%`   |
| Confidence Threshold    | `{IDLE_CONFIDENCE_THRESHOLD}` |
| Blast Radius Limit      | `{BLAST_RADIUS_LIMIT}`    |
""")

    with cc2:
        st.markdown("### 🛡️ Environment Policies")
        policy_rows = []
        for env, pol in ENVIRONMENT_POLICY.items():
            policy_rows.append({
                "Environment": env.upper(),
                "Action": pol["action"],
                "Approval Required": "✅ Yes" if pol["requires_approval"] else "❌ No",
                "Min Severity": pol["severity_floor"],
            })
        st.dataframe(pd.DataFrame(policy_rows), use_container_width=True, hide_index=True)

    st.divider()

    st.markdown("### 💲 Instance Cost Matrix")
    cost_rows = []
    for itype, costs in INSTANCE_COST.items():
        cost_rows.append({
            "Instance Type": itype,
            "USD/Hour": f"${costs['usd_per_hr']:.3f}",
            "INR/Day": f"₹{costs['inr_per_day']:,}",
            "Monthly (USD)": f"${costs['usd_per_hr'] * 720:.2f}",
        })
    st.dataframe(pd.DataFrame(cost_rows), use_container_width=True, hide_index=True)

    st.divider()

    st.markdown("### 📂 File Paths")
    st.code(f"""
VM Data:       {VM_DATA_PATH}
Savings Log:   {SAVINGS_LOG_PATH}
Alert Log:     {ALERT_LOG_PATH}
Database:      {DB_PATH}
""", language="text")


# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption("☁️ Smart Cloud Cost Saver — Phase 2 Prototype Dashboard | Built with Streamlit")
