"""
ChainFlow — routers/documents.py
Document library: lists all PDFs stored in Azure Blob Storage for a tenant.

Endpoints:
  GET /documents/?tenant_id=1   — returns proformas, POs, tax invoices
"""

import logging
from fastapi import APIRouter, HTTPException

logger = logging.getLogger("chainflow.documents")

router = APIRouter()


@router.get("/")
def get_documents(tenant_id: int):
    """
    Returns all procurement documents (proformas, purchase orders, tax invoices)
    for the given tenant, retrieved from Azure Blob Storage.
    Each document includes its type label, file name, size, created_at, and a
    fresh SAS URL valid for 1 hour.
    """
    try:
        from backend.integrations.blob_storage import list_documents, get_sas_url

        prefix = f"tenant-{tenant_id}/"
        folders = [
            ("proformas", "Proforma Invoice"),
            ("purchase-orders", "Purchase Order"),
            ("tax-invoices", "Tax Invoice"),
        ]

        documents = []
        for folder, label in folders:
            items = list_documents(f"{prefix}{folder}/")
            for item in items:
                documents.append({
                    "type": folder,
                    "type_label": label,
                    "name": item["name"].split("/")[-1],
                    "full_path": item["name"],
                    "size_bytes": item["size_bytes"],
                    "created_at": item["created_at"],
                    "url": item["url"],
                })

        # Sort newest first
        documents.sort(key=lambda d: d["created_at"], reverse=True)
        return {"tenant_id": tenant_id, "total": len(documents), "documents": documents}

    except Exception as exc:
        logger.error("get_documents failed for tenant %d: %s", tenant_id, exc)
        raise HTTPException(status_code=500, detail=f"Could not list documents: {exc}")
