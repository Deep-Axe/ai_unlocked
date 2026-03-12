"""
ChainFlow — routers/health.py
GET /health   — parallel readiness probes for DB, Blob, and ACS.
Returns {"status": "healthy"|"degraded", "checks": {...}} with per-service detail.
All blocking I/O is wrapped in run_in_executor so asyncio.gather runs them
truly in parallel without blocking the event loop.
"""

import os
import asyncio
import logging

from fastapi import APIRouter
from sqlalchemy import text

logger = logging.getLogger("chainflow.health")

router = APIRouter()


async def _check_db() -> dict:
    """Runs SELECT 1 in a thread so the event loop is never blocked."""
    def _sync():
        from backend.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            return {"status": "ok"}
        finally:
            db.close()

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _sync), timeout=2.0
        )
    except Exception as exc:
        logger.warning("Health DB check failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


async def _check_blob() -> dict:
    """Calls get_service_properties() in a thread — lightweight, no pagination."""
    def _sync():
        from azure.storage.blob import BlobServiceClient
        conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
        if not conn_str:
            raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING not set")
        client = BlobServiceClient.from_connection_string(conn_str)
        client.get_service_properties()
        return {"status": "ok"}

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _sync), timeout=2.0
        )
    except Exception as exc:
        logger.warning("Health Blob check failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


async def _check_acs() -> dict:
    """
    Validates the ACS connection string by parsing it and confirming the key
    is decodable. A real send probe is skipped — the Azure-managed sandbox domain
    requires recipient verification in the portal before emails reach inboxes.
    """
    def _sync():
        import base64
        conn_str = os.environ.get("ACS_CONNECTION_STRING", "")
        if not conn_str:
            raise RuntimeError("ACS_CONNECTION_STRING not set")
        parts = dict(p.split("=", 1) for p in conn_str.split(";") if "=" in p)
        if "endpoint" not in parts or "accesskey" not in parts:
            raise RuntimeError("ACS_CONNECTION_STRING missing endpoint or accesskey")
        # Confirm the key is valid base64
        base64.b64decode(parts["accesskey"])
        return {"status": "ok"}

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _sync), timeout=2.0
        )
    except Exception as exc:
        logger.warning("Health ACS check failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


@router.get("/health")
async def health_check():
    """
    Parallel readiness probe.
    Returns 200 with status='healthy' when all checks pass,
    or 200 with status='degraded' if any check fails (so the UI can still poll).
    """
    db_result, blob_result, acs_result = await asyncio.gather(
        _check_db(),
        _check_blob(),
        _check_acs(),
    )

    all_ok = all(
        r["status"] == "ok" for r in [db_result, blob_result, acs_result]
    )

    return {
        "status": "healthy" if all_ok else "degraded",
        "checks": {
            "database": db_result,
            "blob_storage": blob_result,
            "acs_email": acs_result,
        },
    }
