"""
ml/ranker.py
============
Phase 2 — ML-based VM ranking (shadow mode).

Uses Isolation Forest from scikit-learn to score VMs on anomaly/waste
likelihood. In shadow mode, scores enrich VM dicts but don't drive
decisions (the policy engine still makes all final calls).

Graceful fallback if scikit-learn is not installed.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _extract_features(vm: dict) -> list:
    """Extract numeric features from a VM dict for the ML model."""
    metrics = vm.get("metrics") or vm
    ca = vm.get("cost_analysis", {})

    return [
        float(metrics.get("cpu_usage", 50)),
        float(metrics.get("ram_usage", 50)),
        float(metrics.get("gpu_usage", 0)),
        float(metrics.get("network_in_mbps", 0) or 0)
            + float(metrics.get("network_out_mbps", 0) or 0),
        float(metrics.get("storage_read_mbps", 0) or 0)
            + float(metrics.get("storage_write_mbps", 0) or 0),
        float(vm.get("idle_score", 0)),
        float(vm.get("decision_confidence", 0)),
        float(ca.get("daily_waste_usd", 0)),
        float(ca.get("waste_so_far_inr", 0)),
    ]


def score_vms(vms: list) -> list:
    """
    Score VMs using Isolation Forest.
    Enriches each VM with: ml_score, ml_waste_30d_usd, ml_rank.

    Falls back to heuristic scoring if sklearn is unavailable.
    """
    if not vms:
        print("  [ml] No VMs to score.")
        return vms

    try:
        import numpy as np
        from sklearn.ensemble import IsolationForest

        features = [_extract_features(vm) for vm in vms]
        X = np.array(features, dtype=float)

        # Isolation Forest: lower score = more anomalous (more likely waste)
        n_samples = len(X)
        n_estimators = min(100, max(10, n_samples * 2))
        contamination = min(0.5, max(0.1, 0.3))

        model = IsolationForest(
            n_estimators=n_estimators,
            contamination=contamination,
            random_state=42,
        )
        model.fit(X)

        # score_samples returns negative for anomalies, positive for normal
        raw_scores = model.score_samples(X)

        # Normalize to 0–1 where 1 = most anomalous (most likely waste)
        min_s, max_s = raw_scores.min(), raw_scores.max()
        if max_s - min_s > 0:
            normalized = 1.0 - (raw_scores - min_s) / (max_s - min_s)
        else:
            normalized = np.full_like(raw_scores, 0.5)

        # Rank (1 = highest waste likelihood)
        ranked_indices = np.argsort(-normalized)
        ranks = np.empty_like(ranked_indices)
        ranks[ranked_indices] = np.arange(1, len(vms) + 1)

        for i, vm in enumerate(vms):
            ca = vm.get("cost_analysis", {})
            daily_usd = ca.get("daily_waste_usd", 0)
            score = float(normalized[i])
            vm["ml_score"] = round(score, 4)
            vm["ml_waste_30d_usd"] = round(daily_usd * 30 * score, 2)
            vm["ml_rank"] = int(ranks[i])

        print(f"  [ml] 🧠  Isolation Forest scored {len(vms)} VMs "
              f"(n_estimators={n_estimators}, contamination={contamination:.1f})")

        # Print top 5
        sorted_vms = sorted(vms, key=lambda v: v.get("ml_rank", 999))
        for v in sorted_vms[:5]:
            print(f"       #{v['ml_rank']:>2}  {v['name']:<28}  "
                  f"score:{v['ml_score']:.3f}  "
                  f"waste_30d:${v['ml_waste_30d_usd']:,.2f}")

        return vms

    except ImportError:
        print("  [ml] ⚠️  scikit-learn not installed — using heuristic fallback")
        return _heuristic_fallback(vms)
    except Exception as e:
        print(f"  [ml] ⚠️  ML scoring error: {e} — using heuristic fallback")
        return _heuristic_fallback(vms)


def _heuristic_fallback(vms: list) -> list:
    """Simple heuristic scoring when sklearn is not available."""
    for i, vm in enumerate(vms):
        ca = vm.get("cost_analysis", {})
        idle_score = float(vm.get("idle_score", 0))
        confidence = float(vm.get("decision_confidence", 0))
        daily_usd = ca.get("daily_waste_usd", 0)

        # Weighted heuristic
        score = (idle_score * 0.4 + confidence * 0.4
                 + min(daily_usd / 10.0, 1.0) * 0.2)
        vm["ml_score"] = round(score, 4)
        vm["ml_waste_30d_usd"] = round(daily_usd * 30 * score, 2)

    # Rank by score descending
    sorted_indices = sorted(range(len(vms)),
                            key=lambda i: vms[i].get("ml_score", 0),
                            reverse=True)
    for rank, idx in enumerate(sorted_indices, 1):
        vms[idx]["ml_rank"] = rank

    print(f"  [ml] 📊  Heuristic scored {len(vms)} VMs (fallback mode)")
    sorted_vms = sorted(vms, key=lambda v: v.get("ml_rank", 999))
    for v in sorted_vms[:5]:
        print(f"       #{v['ml_rank']:>2}  {v['name']:<28}  "
              f"score:{v['ml_score']:.3f}  "
              f"waste_30d:${v['ml_waste_30d_usd']:,.2f}")

    return vms
