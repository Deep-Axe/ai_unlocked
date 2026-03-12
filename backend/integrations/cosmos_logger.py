"""
ChainFlow — integrations/cosmos_logger.py
Logs every GPT-OSS-120B call to Azure Cosmos DB for audit and feedback.

Schema per document:
{
  "id":                 "uuid",
  "tenant_id":          "1",              <- partition key (string)
  "call_type":          "watchdog"
                      | "quote_comparison"
                      | "policy_evaluation"
                      | "rejection_feedback",
  "model":              "gpt-oss-120b",
  "sku_code":           "DRAW-CORD-3MM",
  "input_summary":      "...",            <- abbreviated prompt context
  "output":             "...",            <- full model response
  "winner":             "Punjab...",      <- quote_comparison only
  "reasoning":          "...",
  "policy_tier":        "auto" | "rohan" | "harpreet",  <- policy_evaluation only
  "policy_decision":    "approved" | "pending_rohan" | "pending_harpreet",
  "latency_ms":         1240,
  "prompt_tokens":      312,
  "completion_tokens":  89,
  "timestamp":          "2026-03-10T12:34:56Z",
  "recommendation_id":  6,
  "feedback":           null | "rejected: insufficient stock data"
}

Never raises — logging failure must never block the procurement workflow.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("chainflow.cosmos")


def _get_container():
    from azure.cosmos import CosmosClient

    # Prefer endpoint + key format; fall back to connection string
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    key = os.environ.get("COSMOS_KEY")

    if endpoint and key:
        client = CosmosClient(endpoint, credential=key)
    else:
        conn_str = os.environ.get("COSMOS_CONNECTION_STRING")
        if not conn_str:
            return None
        client = CosmosClient.from_connection_string(conn_str)

    database = client.get_database_client(
        os.environ.get("COSMOS_DATABASE", "chainflow")
    )
    return database.get_container_client(
        os.environ.get("COSMOS_CONTAINER", "ai_logs")
    )


def log_watchdog_call(
    tenant_id: int,
    sku_code: str,
    recommendation_id: int,
    input_summary: str,
    reasoning: str,
    latency_ms: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> Optional[str]:
    """Log a watchdog agent reasoning call. Never raises."""
    try:
        container = _get_container()
        if container is None:
            logger.warning("Cosmos not configured — skipping watchdog log")
            return None

        doc_id = str(uuid.uuid4())
        container.create_item(body={
            "id":                doc_id,
            "tenant_id":         str(tenant_id),
            "call_type":         "watchdog",
            "model":             os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-oss-120b"),
            "sku_code":          sku_code,
            "input_summary":     input_summary[:500],
            "output":            reasoning,
            "winner":            None,
            "reasoning":         reasoning,
            "policy_tier":       None,
            "policy_decision":   None,
            "latency_ms":        latency_ms,
            "prompt_tokens":     prompt_tokens,
            "completion_tokens": completion_tokens,
            "timestamp":         datetime.now(timezone.utc).isoformat(),
            "recommendation_id": recommendation_id,
            "feedback":          None,
        })
        logger.info("Cosmos: watchdog rec#%d (%dms)", recommendation_id, latency_ms)
        return doc_id

    except Exception as exc:
        logger.warning("Cosmos log failed (watchdog): %s", exc)
        return None


def log_quote_comparison(
    tenant_id: int,
    sku_code: str,
    recommendation_id: int,
    quotes_summary: str,
    winner: str,
    reasoning: str,
    latency_ms: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> Optional[str]:
    """Log a quote comparison call. Never raises."""
    try:
        container = _get_container()
        if container is None:
            logger.warning("Cosmos not configured — skipping quote comparison log")
            return None

        doc_id = str(uuid.uuid4())
        container.create_item(body={
            "id":                doc_id,
            "tenant_id":         str(tenant_id),
            "call_type":         "quote_comparison",
            "model":             os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-oss-120b"),
            "sku_code":          sku_code,
            "input_summary":     quotes_summary[:500],
            "output":            reasoning,
            "winner":            winner,
            "reasoning":         reasoning,
            "policy_tier":       None,
            "policy_decision":   None,
            "latency_ms":        latency_ms,
            "prompt_tokens":     prompt_tokens,
            "completion_tokens": completion_tokens,
            "timestamp":         datetime.now(timezone.utc).isoformat(),
            "recommendation_id": recommendation_id,
            "feedback":          None,
        })
        logger.info("Cosmos: quote_comparison rec#%d winner=%s (%dms)",
                    recommendation_id, winner, latency_ms)
        return doc_id

    except Exception as exc:
        logger.warning("Cosmos log failed (quote_comparison): %s", exc)
        return None


def log_policy_evaluation(
    tenant_id: int,
    sku_code: str,
    recommendation_id: int,
    order_value: float,
    policy_tier: str,
    policy_decision: str,
    reasoning: str,
    latency_ms: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> Optional[str]:
    """
    Log a spend policy evaluation call.
    policy_tier: "auto" | "rohan" | "harpreet"
    policy_decision: "approved" | "pending_rohan" | "pending_harpreet"
    Never raises.
    """
    try:
        container = _get_container()
        if container is None:
            logger.warning("Cosmos not configured — skipping policy log")
            return None

        doc_id = str(uuid.uuid4())
        container.create_item(body={
            "id":                doc_id,
            "tenant_id":         str(tenant_id),
            "call_type":         "policy_evaluation",
            "model":             os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-oss-120b"),
            "sku_code":          sku_code,
            "input_summary":     f"Order value: Rs {order_value:,.2f} — tier: {policy_tier}",
            "output":            reasoning,
            "winner":            None,
            "reasoning":         reasoning,
            "policy_tier":       policy_tier,
            "policy_decision":   policy_decision,
            "latency_ms":        latency_ms,
            "prompt_tokens":     prompt_tokens,
            "completion_tokens": completion_tokens,
            "timestamp":         datetime.now(timezone.utc).isoformat(),
            "recommendation_id": recommendation_id,
            "feedback":          None,
        })
        logger.info("Cosmos: policy_evaluation rec#%d tier=%s decision=%s (%dms)",
                    recommendation_id, policy_tier, policy_decision, latency_ms)
        return doc_id

    except Exception as exc:
        logger.warning("Cosmos log failed (policy_evaluation): %s", exc)
        return None


def write_rejection_feedback(
    tenant_id: int,
    recommendation_id: int,
    sku_code: str,
    reason: Optional[str] = None,
) -> None:
    """
    When Rohan rejects a recommendation, patch the most recent Cosmos log
    entry for this recommendation with a feedback note.
    Future watchdog runs for this SKU will see this feedback in their prompt.
    Never raises.
    """
    try:
        container = _get_container()
        if container is None:
            return

        query = (
            "SELECT TOP 1 * FROM c "
            "WHERE c.tenant_id = @tid AND c.recommendation_id = @rid "
            "ORDER BY c.timestamp DESC"
        )
        items = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@tid", "value": str(tenant_id)},
                {"name": "@rid", "value": recommendation_id},
            ],
            enable_cross_partition_query=False,
            partition_key=str(tenant_id),
        ))

        feedback_text = reason or "Rejected by procurement manager."

        if not items:
            container.create_item(body={
                "id":                str(uuid.uuid4()),
                "tenant_id":         str(tenant_id),
                "call_type":         "rejection_feedback",
                "model":             "n/a",
                "sku_code":          sku_code,
                "input_summary":     "",
                "output":            "",
                "winner":            None,
                "reasoning":         "",
                "policy_tier":       None,
                "policy_decision":   None,
                "latency_ms":        0,
                "prompt_tokens":     0,
                "completion_tokens": 0,
                "timestamp":         datetime.now(timezone.utc).isoformat(),
                "recommendation_id": recommendation_id,
                "feedback":          feedback_text,
            })
            return

        item = items[0]
        item["feedback"] = feedback_text
        container.replace_item(item=item["id"], body=item)
        logger.info("Rejection feedback written for rec#%d", recommendation_id)

    except Exception as exc:
        logger.warning("Cosmos rejection feedback failed: %s", exc)


def get_recent_rejection_feedback(
    tenant_id: int,
    sku_code: str,
    limit: int = 3,
) -> list[str]:
    """
    Retrieve recent rejection feedback for a SKU.
    Injected into future watchdog prompts for this SKU.
    Never raises — returns [] on any error.
    """
    try:
        container = _get_container()
        if container is None:
            return []

        query = (
            f"SELECT TOP {limit} c.feedback, c.sku_code, c.timestamp "
            "FROM c "
            "WHERE c.tenant_id = @tid "
            "AND c.sku_code = @sku "
            "AND IS_DEFINED(c.feedback) AND c.feedback != null "
            "ORDER BY c.timestamp DESC"
        )
        items = list(container.query_items(
            query=query,
            parameters=[
                {"name": "@tid", "value": str(tenant_id)},
                {"name": "@sku", "value": sku_code},
            ],
            enable_cross_partition_query=False,
            partition_key=str(tenant_id),
        ))
        return [item["feedback"] for item in items if item.get("feedback")]

    except Exception as exc:
        logger.warning("get_recent_rejection_feedback failed: %s", exc)
        return []


def get_ai_log(tenant_id: int, limit: int = 50) -> list[dict]:
    """
    Retrieve recent AI log entries for the frontend AI Log tab.
    Includes watchdog, quote_comparison, and policy_evaluation entries.
    Excludes standalone rejection_feedback records (no reasoning content).
    Never raises — returns [] on any error.
    """
    try:
        container = _get_container()
        if container is None:
            return []

        query = (
            f"SELECT TOP {limit} * FROM c "
            "WHERE c.tenant_id = @tid "
            "AND c.call_type != 'rejection_feedback' "
            "ORDER BY c.timestamp DESC"
        )
        items = list(container.query_items(
            query=query,
            parameters=[{"name": "@tid", "value": str(tenant_id)}],
            enable_cross_partition_query=False,
            partition_key=str(tenant_id),
        ))
        return items

    except Exception as exc:
        logger.warning("get_ai_log failed: %s", exc)
        return []
