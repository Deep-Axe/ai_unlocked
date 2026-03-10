"""
ChainFlow — scoring/vendor_scorer.py
Dynamic vendor performance scoring from transaction history.

Formula
───────
    on_time_rate     = on_time_deliveries / total_orders
    quality_penalty  = quality_issues / total_orders
    score            = (on_time_rate × 70) + ((1 − quality_penalty) × 30)
    result           = clamped to [0.0, 100.0], rounded to 2 decimal places

Weight rationale
────────────────
On-time delivery carries 70% of the score because late deliveries are the
primary cause of production stoppages for MSMEs. A vendor who delivers late
but always delivers perfect quality is still operationally damaging — Rohan
cannot run a line without materials.

Quality issues carry 30% because defective components cause rework, waste, and
downstream customer complaints, but the production line is not immediately
halted the way it is by a missing delivery.

Neutral baseline (50.0)
───────────────────────
New vendors with zero transaction history return 50.0. This allows them to
receive lower-stakes orders and begin building a track record without being
artificially penalised for being new. As transaction data accumulates the score
converges to the actual performance level.

Zero FastAPI / SQLAlchemy imports — this module is pure Python.
"""


def compute_vendor_score(
    total_orders: int,
    on_time_deliveries: int,
    quality_issues: int,
) -> float:
    """
    Compute a 0–100 vendor performance score from transaction counters.

    Args:
        total_orders:       Total number of orders placed with this vendor.
        on_time_deliveries: Orders delivered on or before the agreed date.
        quality_issues:     Orders that had a documented quality problem.

    Returns:
        Float in [0.0, 100.0] rounded to 2 decimal places.
        Returns 50.0 when total_orders == 0 (neutral baseline, no history).

    Edge cases:
        on_time_deliveries > total_orders — should not occur in practice;
        the clamp handles it gracefully (score capped at 100.0).
        quality_issues > total_orders — same; clamp handles it (score floored
        at 0.0).
    """
    if total_orders == 0:
        return 50.0

    on_time_rate = on_time_deliveries / total_orders
    quality_penalty = quality_issues / total_orders

    score = (on_time_rate * 70.0) + ((1.0 - quality_penalty) * 30.0)

    # Clamp to valid range — defensive against impossible counter states
    score = max(0.0, min(100.0, score))

    return round(score, 2)


# ──────────────────────────────────────────────────────────────────────────────
# Self-test
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # No history → neutral baseline
    assert compute_vendor_score(0, 0, 0) == 50.0, "FAIL: no history should return 50.0"

    # Perfect record: 10/10 on time, 0 quality issues
    # on_time_rate=1.0 → 1.0×70=70 | quality_penalty=0 → 1.0×30=30 | total=100.0
    assert compute_vendor_score(10, 10, 0) == 100.0, "FAIL: perfect record should return 100.0"

    # Worst case: 0/10 on time, 10/10 quality issues
    # on_time_rate=0 → 0×70=0 | quality_penalty=1.0 → 0×30=0 | total=0.0
    assert compute_vendor_score(10, 0, 10) == 0.0, "FAIL: worst case should return 0.0"

    # Realistic case: 10 orders, 8 on time, 1 quality issue
    # on_time_rate=0.8 → 0.8×70=56.0
    # quality_penalty=0.1 → (1-0.1)×30=27.0
    # total = 83.0
    result = compute_vendor_score(10, 8, 1)
    assert result == 83.0, f"FAIL: expected 83.0, got {result}"

    # Partial on-time, no quality issues
    # 5/10 on time, 0 quality issues → 0.5×70 + 1.0×30 = 35+30 = 65.0
    assert compute_vendor_score(10, 5, 0) == 65.0, "FAIL: 50% on-time should return 65.0"

    # Clamp test — on_time > total (defensive)
    assert compute_vendor_score(5, 6, 0) == 100.0, "FAIL: should clamp at 100.0"

    print("All assertions passed.")
    print()
    print("Sample scores:")
    cases = [
        (0,  0,  0,  "No history"),
        (10, 10, 0,  "Perfect"),
        (10, 8,  1,  "Good (realistic)"),
        (10, 5,  2,  "Average"),
        (10, 3,  4,  "Poor"),
        (10, 0,  10, "Worst case"),
    ]
    for total, ontime, quality, label in cases:
        score = compute_vendor_score(total, ontime, quality)
        print(f"  {label:<22} → {score:6.2f}")
