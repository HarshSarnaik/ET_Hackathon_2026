"""
main.py — Phase 2 Orchestrator
================================
Smart Cloud Cost Saver — Phase 2

Pipeline:
  DB Init + Observability
  → Fetch → Metrics Quality Gate → Detect → Cost Calc
  → ML Ranking (shadow) → Policy Engine → Decisions
  → Jira Tickets → Twilio Alerts → Execute → DB Persist → Report

Run:
    pip install flask twilio scikit-learn numpy pyyaml
    python main.py
"""

import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))

# Fix Windows console encoding
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


from modules.fetch_data       import fetch_data
from modules.metrics_quality  import run_quality_gate
from modules.detect_idle      import detect_idle
from modules.cost_calc        import calculate_all
from modules.decision         import decide_all
from modules.twilio_notify    import notify_all
from modules.executor         import execute_all
from modules.logger           import print_savings_report
from modules.approval_server  import start_approval_server, start_escalation_watchdog
from modules.jira_integration import create_tickets_for_batch
from modules.observability    import start_metrics_server, update as obs_update
from ml.ranker                import score_vms
from db.store                 import (
    init_db, start_run, finish_run,
    upsert_vm, upsert_detection, record_action,
    register_approval, record_alert, record_feedback,
    upsert_ml_score, get_cumulative_savings, get_precision_stats,
)
from config.settings import USE_MOCK, APPROVAL_PORT, METRICS_SERVER_PORT

BANNER = """
╔══════════════════════════════════════════════════════════════════════╗
║   SMART CLOUD COST SAVER  —  PHASE 2                                ║
║   DB + Policy Engine + ML Ranking + Jira + Observability            ║
╚══════════════════════════════════════════════════════════════════════╝
"""

def run_pipeline():
    print(BANNER)
    mode = "MOCK" if USE_MOCK else "LIVE AWS"
    print(f"  Mode: {mode}\n")
    t0 = time.time()

    # 1. Bootstrap
    print("--- STEP 1: Bootstrap ---")
    init_db()
    run_id = start_run(mode="mock" if USE_MOCK else "live")
    print(f"  Correlation ID: {run_id}")
    start_metrics_server(background=True)
    start_approval_server(background=True)
    start_escalation_watchdog()
    print(f"  Metrics   -> http://localhost:{METRICS_SERVER_PORT}/dashboard")
    print(f"  Approvals -> http://localhost:{APPROVAL_PORT}/status\n")

    # 2. Fetch
    print("--- STEP 2: Fetch VM Data ---")
    all_vms = fetch_data()
    obs_update("vms_scanned_total", len(all_vms), delta=True)
    for vm in all_vms: upsert_vm(run_id, vm)

    # 3. Metrics quality gate
    print("\n--- STEP 3: Metrics Quality Gate ---")
    quality_vms, dropped_vms = run_quality_gate(all_vms)
    for vm in quality_vms: upsert_vm(run_id, vm)

    # 4. Idle detection
    print("\n--- STEP 4: Idle Detection ---")
    idle_vms = detect_idle(quality_vms)
    obs_update("vms_idle_detected_total", len(idle_vms), delta=True)
    for vm in idle_vms: upsert_detection(run_id, vm)

    if not idle_vms:
        print("\n  No idle VMs detected.")
        _finalize(run_id, t0, [], [], [])
        return

    # 5. Cost calc
    print("\n--- STEP 5: Cost Calculator ---")
    enriched = calculate_all(idle_vms)

    # 6. ML ranking (shadow mode)
    print("\n--- STEP 6: ML Ranking (shadow) ---")
    enriched = score_vms(enriched)
    for vm in enriched:
        if vm.get("ml_score") is not None:
            upsert_ml_score(run_id, vm["resource_id"], {
                "isolation_score": vm.get("ml_score"),
                "waste_score_30d": vm.get("ml_waste_30d_usd"),
                "ml_rank": vm.get("ml_rank"),
            })

    # 7. Policy decisions
    print("\n--- STEP 7: Policy Engine v2 ---")
    auto_list, notify_list = decide_all(enriched, run_id)

    # 8. Jira
    jira_map = {}
    if notify_list:
        print("\n--- STEP 8: Jira Tickets ---")
        jira_map = create_tickets_for_batch(notify_list)

    # 9. Twilio alerts
    alert_results = []
    if notify_list:
        print("\n--- STEP 9: Twilio Alerts ---")
        for vm in notify_list:
            register_approval(run_id, vm)
        alert_results = notify_all(notify_list)
        for vm, ar in zip(notify_list, alert_results):
            record_alert(run_id, vm["resource_id"], ar)
        obs_update("vms_notified_total", len(notify_list), delta=True)

    # 10. Execute auto-shutdowns
    action_results = []
    if auto_list:
        print("\n--- STEP 10: Auto-Shutdown ---")
        action_results = execute_all(auto_list)
        for vm, r in zip(auto_list, action_results):
            record_action(run_id, vm, r)
            record_feedback(vm["resource_id"], run_id, "APPROVED", vm, was_correct=True)
        stopped = sum(1 for r in action_results if r.get("success"))
        obs_update("vms_auto_stopped_total", stopped, delta=True)
        obs_update("savings_daily_inr_total", sum(r.get("savings_daily_inr",0) for r in action_results if r.get("success")), delta=True)
        obs_update("savings_daily_usd_total", sum(r.get("savings_daily_usd",0) for r in action_results if r.get("success")), delta=True)

    _finalize(run_id, t0, action_results, notify_list, idle_vms)


def _finalize(run_id, t0, action_results, pending, idle_vms):
    duration = round(time.time() - t0, 2)
    obs_update("last_run_duration_seconds", duration)

    print("\n--- STEP 11: Report ---")
    print_savings_report(action_results, idle_vms, pending)

    stopped   = sum(1 for r in action_results if r.get("success"))
    saved_inr = sum(r.get("savings_daily_inr",0) for r in action_results if r.get("success"))
    finish_run(run_id, {
        "vms_scanned": len(idle_vms) + len(action_results),
        "vms_idle": len(idle_vms), "vms_auto_stopped": stopped,
        "vms_notified": len(pending), "total_savings_inr": saved_inr,
    })

    prec    = get_precision_stats()
    savings = get_cumulative_savings()

    print(f"  Pipeline done in {duration}s  |  run_id: {run_id}")
    print(f"  {stopped} stopped  |  {len(pending)} awaiting approval")
    print(f"  Precision proxy: {prec.get('precision_proxy') or 'N/A'}")
    print(f"  All-time savings: ${savings.get('total_daily_usd') or 0:,.2f} USD")
    print(f"  Dashboard: http://localhost:{METRICS_SERVER_PORT}/dashboard")
    print(f"  Approvals: http://localhost:{APPROVAL_PORT}/status\n")

    if pending:
        print("  Servers running. Ctrl+C to exit.\n")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            print("\n  Shutting down.")


if __name__ == "__main__":
    run_pipeline()
