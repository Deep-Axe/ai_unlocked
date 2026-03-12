"""
ChainFlow — integrations/excel_parser.py
Parses an uploaded .xlsx inventory file and upserts rows into the DB.

Called by POST /inventory/upload/excel in routers/inventory.py.
This module CAN import from fastapi and sqlalchemy — unlike tally_listener.py,
it is not a standalone script.
"""

import io
from datetime import datetime

import openpyxl
from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.models import SKU, InventoryLog
from backend.schemas import ExcelUploadSummary
from backend.scoring.thresholds import get_category_default_multiplier

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

REQUIRED_COLUMNS: list[str] = [
    "sku_code",
    "name",
    "category",
    "unit",
    "current_quantity",
    "reorder_threshold",
    "reorder_quantity",
    "unit_cost",
]

NUMERIC_COLUMNS: set[str] = {
    "current_quantity",
    "reorder_threshold",
    "reorder_quantity",
    "unit_cost",
}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _normalise_header(raw: object) -> str:
    """Lowercase, strip, and replace spaces with underscores for header matching."""
    return str(raw).strip().lower().replace(" ", "_")


def _is_empty(value: object) -> bool:
    """True for None, empty string, or the string 'none' / 'nan'."""
    if value is None:
        return True
    s = str(value).strip().lower()
    return s in ("", "none", "nan")


def _coerce_float(value: object, column: str, row_num: int) -> tuple[float | None, str | None]:
    """
    Try to coerce a cell value to float.

    Returns (float_value, None) on success or (None, error_message) on failure.
    """
    if _is_empty(value):
        return 0.0, None  # treat missing numeric as 0 — Rohan can fill in later
    try:
        return float(value), None  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None, f"Row {row_num}: {column!r} value {value!r} is not a valid number"


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

async def parse_excel_upload(
    file: UploadFile,
    tenant_id: int,
    db: Session,
) -> ExcelUploadSummary:
    """
    Parse an uploaded .xlsx inventory file and upsert each row into the DB.

    Column layout expected (case-insensitive, whitespace-stripped):
        sku_code | name | category | unit | current_quantity |
        reorder_threshold | reorder_quantity | unit_cost

    Upsert logic (keyed on tenant_id + sku_code):
        EXISTS → update current_quantity, unit_cost, reorder_threshold,
                 reorder_quantity, last_updated, source="excel"
                 write InventoryLog (change_source="excel_upload")
                 Note: reorder_threshold and reorder_quantity are updated
                 intentionally — the sheet is the primary way Rohan bulk-
                 configures thresholds across many SKUs at once.
        NEW    → create SKU with source="excel"
                 critical_multiplier set from category default

    Returns ExcelUploadSummary with created/updated/skipped/errors counts.

    Raises HTTPException 400 if:
        - The file cannot be opened as a valid .xlsx workbook
        - Any of the 8 required columns are missing from the header row
    Partial row errors (bad numeric values, etc.) are collected in the
    errors list and do NOT cause a 400 — the rest of the file still processes.
    """
    # ── Read bytes from the upload ────────────────────────────────────────────
    contents = await file.read()

    try:
        wb = openpyxl.load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "Unreadable file", "detail": str(exc)},
        ) from exc

    ws = wb.active
    if ws is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "Empty workbook", "detail": "The uploaded file has no active sheet"},
        )

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(
            status_code=400,
            detail={"error": "Empty sheet", "detail": "The uploaded file contains no rows"},
        )

    # ── Parse and validate headers ────────────────────────────────────────────
    raw_headers = rows[0]
    col_index: dict[str, int] = {
        _normalise_header(h): i
        for i, h in enumerate(raw_headers)
        if h is not None
    }

    missing = [col for col in REQUIRED_COLUMNS if col not in col_index]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={"error": "Missing columns", "detail": missing},
        )

    # ── Process data rows ─────────────────────────────────────────────────────
    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    for row_num, row in enumerate(rows[1:], start=2):  # 1-indexed, row 1 is header
        raw_sku_code = row[col_index["sku_code"]]

        # Skip rows with empty sku_code
        if _is_empty(raw_sku_code):
            skipped += 1
            continue

        sku_code = str(raw_sku_code).strip()

        # ── Coerce numeric fields ─────────────────────────────────────────────
        row_errors: list[str] = []
        numeric_values: dict[str, float] = {}

        for col in NUMERIC_COLUMNS:
            value, err = _coerce_float(row[col_index[col]], col, row_num)
            if err:
                row_errors.append(err)
            else:
                numeric_values[col] = value  # type: ignore[assignment]

        if row_errors:
            errors.extend(row_errors)
            skipped += 1
            continue

        name     = str(row[col_index["name"]]).strip()     if not _is_empty(row[col_index["name"]])     else sku_code
        category = str(row[col_index["category"]]).strip() if not _is_empty(row[col_index["category"]]) else "Raw Material"
        unit     = str(row[col_index["unit"]]).strip()     if not _is_empty(row[col_index["unit"]])     else "units"

        # ── Upsert ────────────────────────────────────────────────────────────
        existing: SKU | None = (
            db.query(SKU)
            .filter(SKU.tenant_id == tenant_id, SKU.sku_code == sku_code)
            .first()
        )

        if existing:
            # Write audit log before mutating the quantity
            if existing.current_quantity != numeric_values["current_quantity"]:
                log = InventoryLog(
                    sku_id=existing.id,
                    previous_quantity=existing.current_quantity,
                    new_quantity=numeric_values["current_quantity"],
                    change_source="excel_upload",
                    changed_at=datetime.utcnow(),
                    notes=f"Uploaded via {file.filename}",
                )
                db.add(log)

            existing.current_quantity = numeric_values["current_quantity"]
            existing.unit_cost        = numeric_values["unit_cost"]
            # Also update thresholds if provided — Rohan may use the sheet to
            # configure reorder levels in bulk.
            existing.reorder_threshold = numeric_values["reorder_threshold"]
            existing.reorder_quantity  = numeric_values["reorder_quantity"]
            existing.last_updated      = datetime.utcnow()
            existing.source            = "excel"
            updated += 1
        else:
            new_sku = SKU(
                tenant_id=tenant_id,
                sku_code=sku_code,
                name=name,
                category=category,
                unit=unit,
                current_quantity=numeric_values["current_quantity"],
                reorder_threshold=numeric_values["reorder_threshold"],
                reorder_quantity=numeric_values["reorder_quantity"],
                unit_cost=numeric_values["unit_cost"],
                critical_multiplier=get_category_default_multiplier(category),
                last_updated=datetime.utcnow(),
                source="excel",
            )
            db.add(new_sku)
            created += 1

    db.commit()

    return ExcelUploadSummary(
        created=created,
        updated=updated,
        skipped=skipped,
        errors=errors,
    )

