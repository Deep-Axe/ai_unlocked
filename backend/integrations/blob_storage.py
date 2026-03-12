"""
ChainFlow — integrations/blob_storage.py
Azure Blob Storage operations for document management.

All documents stored under tenant-scoped paths:
  tenant-1/purchase-orders/PO-2026-0001.pdf
  tenant-1/proformas/PROFORMA-DRAW-CORD-3MM-punjab-components-house.pdf
  tenant-1/tax-invoices/INV-2026-0001.pdf

Blob container is private. All URLs are SAS-signed with 1-hour expiry.
"""

import os
from datetime import datetime, timedelta, timezone

from azure.storage.blob import (
    BlobServiceClient,
    ContentSettings,
    BlobSasPermissions,
    generate_blob_sas,
)


def _get_service_client() -> BlobServiceClient:
    conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    return BlobServiceClient.from_connection_string(conn_str)


def _get_container() -> str:
    return os.environ.get("AZURE_STORAGE_CONTAINER", "chainflow-docs")


def upload_document(blob_path: str, data: bytes,
                    content_type: str = "application/pdf") -> str:
    """
    Upload bytes to Blob Storage at the given path.
    Returns the blob path (not a URL — use get_sas_url to get a viewable URL).
    """
    client = _get_service_client()
    container = _get_container()
    blob_client = client.get_blob_client(container=container, blob=blob_path)
    blob_client.upload_blob(
        data,
        overwrite=True,
        content_settings=ContentSettings(content_type=content_type),
    )
    return blob_path


def get_sas_url(blob_path: str, expiry_hours: int = 1) -> str:
    """
    Generate a time-limited SAS URL for a blob.
    Parses account credentials directly from the connection string to avoid
    any network calls — pure local crypto operation.
    """
    conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    # Parse AccountName and AccountKey from connection string
    parts = {k: v for part in conn_str.split(";") if "=" in part
             for k, v in [part.split("=", 1)]}
    account_name = parts.get("AccountName", "")
    account_key  = parts.get("AccountKey", "")
    container    = _get_container()

    if not account_name or not account_key:
        raise ValueError("Could not resolve storage credentials from connection string")

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container,
        blob_name=blob_path,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
    )
    return f"https://{account_name}.blob.core.windows.net/{container}/{blob_path}?{sas_token}"


def list_documents(prefix: str) -> list[dict]:
    """
    List all blobs under a given prefix.
    Returns metadata including a fresh SAS URL for each blob.
    """
    client = _get_service_client()
    container = _get_container()
    container_client = client.get_container_client(container)

    results = []
    for blob in container_client.list_blobs(name_starts_with=prefix):
        results.append({
            "name": blob.name,
            "size_bytes": blob.size,
            "created_at": blob.creation_time.isoformat() if blob.creation_time else None,
            "url": get_sas_url(blob.name),
        })
    return results


def delete_document(blob_path: str) -> None:
    """Delete a blob. Used by demo-scenario reset."""
    client = _get_service_client()
    container = _get_container()
    blob_client = client.get_blob_client(container=container, blob=blob_path)
    blob_client.delete_blob(delete_snapshots="include")
