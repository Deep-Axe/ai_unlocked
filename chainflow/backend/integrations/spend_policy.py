"""
ChainFlow — integrations/spend_policy.py
GPT-OSS-120B spend policy evaluation engine.

Tiers:
  auto     — order value < Rs 25,000   → approved instantly, no human needed
  rohan    — Rs 25,000 to Rs 1,00,000  → holds at pending_rohan, Rohan approves
  harpreet — order value > Rs 1,00,000 → holds at pending_harpreet, Rohan approves

GPT writes a policy reasoning paragraph for every evaluation.
That paragraph is logged to Cosmos as call_type="policy_evaluation".
Never raises — falls back to rule-based tier on any GPT error.
"""

import logging
import os
import re
import time

logger = logging.getLogger("chainflow.spend_policy")

AUTO_LIMIT   = float(os.environ.get("SPEND_AUTO_LIMIT",   "25000"))
ROHAN_LIMIT  = float(os.environ.get("SPEND_ROHAN_LIMIT", "100000"))


def get_policy(tenant_id: int, db) -> tuple:
    """
    Return (auto_approve_limit, rohan_limit) for the tenant.
    Reads from spend_policies table if a row exists, otherwise falls back
    to env-var defaults.  Always returns plain Python floats (not Decimal).
    """
    from backend.models import SpendPolicy
    policy = db.query(SpendPolicy).filter_by(tenant_id=tenant_id).first()
    if policy:
        return float(policy.auto_approve_limit), float(policy.rohan_limit)
    return AUTO_LIMIT, ROHAN_LIMIT


def evaluate_spend_policy(
    tenant_id: int,
    recommendation_id: int,
    sku_code: str,
    sku_name: str,
    vendor_name: str,
    quantity: float,
    unit: str,
    unit_price: float,
    order_value: float,
    db=None,
) -> dict:
    """
    Evaluate spend policy for a proposed PO.

    Returns:
    {
      "tier":     "auto" | "rohan" | "harpreet",
      "decision": "approved" | "pending_rohan" | "pending_harpreet",
      "reasoning": "<GPT paragraph>",
      "order_value": 175000.0,
      "auto_limit": 25000,
      "rohan_limit": 100000,
    }
    """
    auto_limit, rohan_limit = get_policy(tenant_id, db) if db else (AUTO_LIMIT, ROHAN_LIMIT)

    # Determine tier by value
    if order_value < auto_limit:
        tier = "auto"
        decision = "approved"
    elif order_value <= rohan_limit:
        tier = "rohan"
        decision = "pending_rohan"
    else:
        tier = "harpreet"
        decision = "pending_harpreet"

    reasoning = _gpt_policy_reasoning(
        tenant_id=tenant_id,
        recommendation_id=recommendation_id,
        sku_code=sku_code,
        sku_name=sku_name,
        vendor_name=vendor_name,
        quantity=quantity,
        unit=unit,
        unit_price=unit_price,
        order_value=order_value,
        tier=tier,
        decision=decision,
        auto_limit=auto_limit,
        rohan_limit=rohan_limit,
    )

    return {
        "tier": tier,
        "decision": decision,
        "reasoning": reasoning,
        "order_value": order_value,
        "auto_limit": auto_limit,
        "rohan_limit": rohan_limit,
    }


def _gpt_policy_reasoning(
    tenant_id: int,
    recommendation_id: int,
    sku_code: str,
    sku_name: str,
    vendor_name: str,
    quantity: float,
    unit: str,
    unit_price: float,
    order_value: float,
    tier: str,
    decision: str,
    auto_limit: float = AUTO_LIMIT,
    rohan_limit: float = ROHAN_LIMIT,
) -> str:
    """
    Ask GPT-OSS-120B to write a spend policy reasoning paragraph.
    Logs result to Cosmos. Falls back to a rule-based string on any error.
    Never raises.
    """
    from openai import OpenAI
    from backend.integrations.cosmos_logger import log_policy_evaluation

    tier_explanation = {
        "auto":     f"below the auto-approval threshold of Rs {auto_limit:,.0f}",
        "rohan":    f"between Rs {auto_limit:,.0f} and Rs {rohan_limit:,.0f} — requires Rohan's approval",
        "harpreet": f"above Rs {rohan_limit:,.0f} — high-value order, requires senior approval",
    }[tier]

    prompt = (
        f"You are a spend control AI for Harpreet Hosiery Works, an Indian MSME.\n\n"
        f"A purchase order has been raised:\n"
        f"  SKU: {sku_code} — {sku_name}\n"
        f"  Vendor: {vendor_name}\n"
        f"  Quantity: {int(quantity)} {unit}\n"
        f"  Unit price: Rs {unit_price:,.2f}\n"
        f"  Order value: Rs {order_value:,.2f}\n\n"
        f"Policy decision: {decision.upper().replace('_', ' ')}\n"
        f"Reason: Order value is {tier_explanation}.\n\n"
        f"Write a 2-3 sentence procurement policy note explaining this decision "
        f"in plain business language suitable for the procurement manager. "
        f"Mention the order value, the policy threshold that applies, and what action is required."
    )

    fallback = (
        f"Order value of Rs {order_value:,.2f} for {int(quantity)} {unit} of {sku_code} "
        f"from {vendor_name} is {tier_explanation}. "
        f"Decision: {decision.replace('_', ' ').title()}."
    )

    try:
        client = OpenAI(
            base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
        )
        _t0 = time.time()
        response = client.chat.completions.create(
            model=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-oss-120b"),
            messages=[
                {"role": "system", "content": "You are a procurement policy assistant. Be concise and professional."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
            temperature=0.2,
        )
        _latency_ms = int((time.time() - _t0) * 1000)
        reasoning = (response.choices[0].message.content or "").strip()

        log_policy_evaluation(
            tenant_id=tenant_id,
            sku_code=sku_code,
            recommendation_id=recommendation_id,
            order_value=order_value,
            policy_tier=tier,
            policy_decision=decision,
            reasoning=reasoning,
            latency_ms=_latency_ms,
            prompt_tokens=getattr(getattr(response, "usage", None), "prompt_tokens", 0),
            completion_tokens=getattr(getattr(response, "usage", None), "completion_tokens", 0),
        )
        return reasoning

    except Exception as exc:
        logger.warning("GPT policy reasoning failed: %s — using fallback", exc)
        return fallback
