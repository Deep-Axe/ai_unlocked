"""
ChainFlow — integrations/tally_listener.py
Standalone script that runs on the manufacturer's Windows machine alongside Tally Prime.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 TODO — Step 8  (implement after vendors.py review; NEEDS EXTRA REVIEW PASS)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  TDL/XML parsing is niche — the Tally XML response format is not standard.
    Review the XML parsing logic carefully before shipping.

Architecture:
  - Zero FastAPI imports. This file must run with only:
      requests, schedule, xml.etree.ElementTree, logging, os, dotenv
  - Reads TALLY_URL (default http://localhost:9000) from .env
  - Reads CHAINFLOW_API_URL (default http://localhost:8000) from .env
  - Reads CHAINFLOW_TENANT_ID from .env (required)

Functions to implement:

  fetch_stock_from_tally() -> list[dict]:
    1. Build the XML request (ENVELOPE/HEADER/BODY/EXPORTDATA pattern)
    2. POST to TALLY_URL with Content-Type: application/xml
    3. Parse XML response — key elements to extract per STOCKITEM:
         <NAME>...</NAME>          → stock item name (becomes sku_code after normalisation)
         <CLOSINGBALANCE>...</CLOSINGBALANCE>  → quantity (e.g. "250 Nos" — must split)
         <BASEUNITS>...</BASEUNITS>            → unit of measure
    ⚠️  Tally XML quirks:
         - CLOSINGBALANCE often includes the unit in the string: "250.00 Nos"
           Parse the numeric part only; use BASEUNITS for the unit field.
         - Stock items with zero closing balance ARE included — don't skip them,
           they represent real depletions.
         - Item names may contain special chars: strip leading/trailing whitespace.
    4. Normalise name → sku_code: uppercase, replace spaces with "-", strip punctuation
    5. Return list of dicts matching TallySyncPayload.items schema

  sync_to_chainflow(items: list[dict], tenant_id: int) -> dict:
    POST {CHAINFLOW_API_URL}/inventory/sync/tally
    Body: {"tenant_id": tenant_id, "items": items}
    Returns the response JSON from ChainFlow.

  run_sync_cycle() -> None:
    Orchestrates one full cycle:
      1. Call fetch_stock_from_tally()
      2. Call sync_to_chainflow()
      3. Log: timestamp + item count to tally_sync.log
    If Tally is unreachable: log ConnectionError, return (retry next cycle).
    If ChainFlow is unreachable: log ConnectionError, return.
    Never raise — caller is the scheduler.

Scheduling (main block):
  schedule.every(5).minutes.do(run_sync_cycle)
  Run once immediately on startup, then every 5 minutes.

Logging:
  File handler → tally_sync.log (same directory as this script)
  Console handler → stdout
  Format: "%(asctime)s [%(levelname)s] %(message)s"
  Level: INFO

⚠️  REVIEW CHECKLIST before approving this file:
  [ ] CLOSINGBALANCE parsing handles "250.00 Nos" and pure "250.00" formats
  [ ] sku_code normalisation is deterministic and reversible enough for upsert matching
  [ ] ConnectionError from Tally does NOT crash the process
  [ ] Log file is appended to, not overwritten, across restarts
  [ ] No SQLAlchemy / FastAPI imports anywhere in this file
"""

"""
ChainFlow — integrations/tally_listener.py
Standalone script that runs on the manufacturer's Windows machine alongside Tally Prime.

Architecture:
  - Zero FastAPI/SQLAlchemy imports.  Dependencies: requests, schedule,
    xml.etree.ElementTree (stdlib), logging (stdlib), os (stdlib), dotenv.
  - Reads TALLY_URL           (default http://localhost:9000) from .env
  - Reads CHAINFLOW_API_URL   (default http://localhost:8000) from .env
  - Reads CHAINFLOW_TENANT_ID from .env  (required — script exits if missing)

Run:
  cd chainflow
  python -m backend.integrations.tally_listener

  Or directly:
  python backend/integrations/tally_listener.py

  The script runs one sync immediately on startup, then repeats every 5 minutes.

Log file: tally_sync.log (written to the same directory as this script).
          Opened in append mode — survives restarts without losing history.

⚠️  TDL/XML parsing is niche — the Tally response format is not standard XML.
    See fetch_stock_from_tally() and _parse_tally_xml() for format notes.
"""

import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
import schedule
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

TALLY_URL: str = os.getenv("TALLY_URL", "http://localhost:9000")
CHAINFLOW_API_URL: str = os.getenv("CHAINFLOW_API_URL", "http://localhost:8000")
CHAINFLOW_TENANT_ID: str | None = os.getenv("CHAINFLOW_TENANT_ID")

_REQUEST_TIMEOUT: int = 10   # seconds — Tally is local; should be fast

# ──────────────────────────────────────────────────────────────────────────────
# Logging (file + console, append mode)
# ──────────────────────────────────────────────────────────────────────────────

_LOG_PATH = Path(__file__).parent / "tally_sync.log"

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

_file_handler = logging.FileHandler(_LOG_PATH, mode="a", encoding="utf-8")
_file_handler.setFormatter(_fmt)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_fmt)

logger = logging.getLogger("tally_listener")
logger.setLevel(logging.INFO)
logger.addHandler(_file_handler)
logger.addHandler(_console_handler)

# ──────────────────────────────────────────────────────────────────────────────
# Tally XML request template
#
# Tally Prime exposes an HTTP server (port 9000 by default) that accepts
# XML envelopes.  The COLLECTION request below asks for every STOCKITEM with
# its CLOSINGBALANCE and BASEUNITS fields.
#
# Tally XML quirks documented here so future maintainers don't re-discover them:
#   1. The root tag is <ENVELOPE>, NOT <RESPONSE>.
#   2. Items live under ENVELOPE > BODY > DATA > COLLECTION > STOCKITEM.
#      (Some Tally versions use TALLYMESSAGE instead of DATA — see parser note.)
#   3. NAME may be wrapped in CDATA or appear as a plain text node.
#   4. CLOSINGBALANCE is typically "250.00 Nos" (numeric then unit string).
#      It can also be plain "250.00" if BASEUNITS is set separately.
#   5. Items with zero closing balance ARE included — do NOT skip them.
# ──────────────────────────────────────────────────────────────────────────────

_TALLY_REQUEST_XML = """\
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export Data</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>List of Stock Items</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION ISMODIFY="No" NAME="List of Stock Items">
            <TYPE>Stock Item</TYPE>
            <NATIVEMETHOD>CLOSINGBALANCE</NATIVEMETHOD>
            <NATIVEMETHOD>BASEUNITS</NATIVEMETHOD>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""


# ──────────────────────────────────────────────────────────────────────────────
# Public helpers
# ──────────────────────────────────────────────────────────────────────────────


def tally_name_to_sku_code(name: str) -> str:
    """
    Derive a stable internal sku_code from a raw Tally stock item name.

    Examples:
        "Nylon Fitting 12mm"  →  "NYLON-FITTING-12MM"
        "Cotton Yarn (40s)"   →  "COTTON-YARN-40S"
        "  Elastic  Tape  "   →  "ELASTIC-TAPE"

    Steps:
        1. Strip leading/trailing whitespace
        2. Remove all characters that are not alphanumeric or space
           (strips parentheses, slashes, dots, etc.)
        3. Collapse one or more consecutive spaces into a single hyphen
        4. Uppercase

    WHY two separate fields (name vs sku_code):
        name     — the original Tally string as Rohan sees it in the dashboard.
                   Human-readable; may have mixed case and punctuation.
        sku_code — normalised upsert key used to match rows across syncs.
                   If both fields received the same raw string, newly created
                   SKUs would display "NYLON-FITTING-12MM" as their name in
                   the dashboard — confusing and hard to read.
    """
    name = name.strip()
    name = re.sub(r"[^a-zA-Z0-9 ]", "", name)   # remove non-alphanumeric (keep spaces)
    name = re.sub(r"\s+", "-", name)              # collapse whitespace → hyphen
    return name.upper()


def _parse_closing_balance(raw: str) -> float:
    """
    Extract the numeric part from a Tally CLOSINGBALANCE string.

    Tally typically returns "250.00 Nos" or "1500.50 Kg" — the quantity
    is the first whitespace-delimited token.  Handles plain "250.00" too.

    Returns 0.0 on any parse failure (prefer a logged no-op over a crash).

        "250.00 Nos"  → 250.0
        "1500.50 Kg"  → 1500.5
        "0.00"        → 0.0
        ""            → 0.0
        "N/A"         → 0.0  (logged as warning)
    """
    raw = (raw or "").strip()
    if not raw:
        return 0.0
    token = raw.split()[0]   # take numeric part before unit string
    try:
        return float(token)
    except ValueError:
        logger.warning("Could not parse CLOSINGBALANCE value %r — defaulting to 0.0", raw)
        return 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Core functions
# ──────────────────────────────────────────────────────────────────────────────

def _parse_tally_xml(xml_text: str) -> list[dict]:
    """
    Parse raw Tally XML response text into a list of item dicts.

    Expected structure (simplified):
        <ENVELOPE>
          <BODY>
            <DATA>
              <COLLECTION>
                <STOCKITEM NAME="Nylon Fitting 12mm">
                  <NAME>Nylon Fitting 12mm</NAME>
                  <CLOSINGBALANCE>250.00 Nos</CLOSINGBALANCE>
                  <BASEUNITS>Nos</BASEUNITS>
                </STOCKITEM>
                ...
              </COLLECTION>
            </DATA>
          </BODY>
        </ENVELOPE>

    Some Tally versions wrap COLLECTION inside TALLYMESSAGE rather than DATA.
    Both layouts are handled by searching for STOCKITEM at any depth.

    Returns a list of dicts ready to pass to TallySyncPayload:
        [{"sku_code": "NYLON-FITTING-12MM",
          "name": "Nylon Fitting 12mm",
          "quantity": 250.0,
          "unit": "Nos"}, ...]
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.error("Tally XML parse error: %s", exc)
        return []

    items: list[dict] = []

    # iter() searches at any depth — handles both DATA and TALLYMESSAGE layouts
    for stock_item in root.iter("STOCKITEM"):
        # NAME can be the tag's NAME attribute OR a child <NAME> element
        name_elem = stock_item.find("NAME")
        name_raw: str = (
            (name_elem.text or "").strip()
            if name_elem is not None
            else stock_item.get("NAME", "").strip()
        )
        if not name_raw:
            logger.debug("Skipping STOCKITEM with no name: %s", ET.tostring(stock_item))
            continue

        closing_elem = stock_item.find("CLOSINGBALANCE")
        closing_raw: str = (closing_elem.text or "").strip() if closing_elem is not None else "0"

        unit_elem = stock_item.find("BASEUNITS")
        unit_raw: str = (unit_elem.text or "").strip() if unit_elem is not None else "units"

        quantity = _parse_closing_balance(closing_raw)

        items.append({
            "sku_code": tally_name_to_sku_code(name_raw),
            "name": name_raw,
            "quantity": quantity,
            "unit": unit_raw or "units",
        })

    return items


def fetch_stock_from_tally() -> list[dict]:
    """
    POST the stock-export request to Tally and return parsed item dicts.

    Raises:
        requests.exceptions.ConnectionError — if Tally is unreachable.
        requests.exceptions.Timeout         — if Tally takes > 10 s.
        requests.exceptions.HTTPError       — if Tally returns 4xx/5xx.

    Callers (run_sync_cycle) catch all of these and log without crashing.

    The raw XML text is logged at DEBUG level so an admin can inspect it
    with LOG_LEVEL=DEBUG when troubleshooting a parsing issue.
    """
    logger.debug("Sending stock export request to %s", TALLY_URL)
    response = requests.post(
        TALLY_URL,
        data=_TALLY_REQUEST_XML.encode("utf-8"),
        headers={"Content-Type": "application/xml"},
        timeout=_REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    xml_text = response.text
    logger.debug("Tally raw response (%d bytes):\n%s", len(xml_text), xml_text[:2000])

    items = _parse_tally_xml(xml_text)
    logger.info("Tally returned %d stock items", len(items))
    return items


def sync_to_chainflow(items: list[dict], tenant_id: int) -> dict:
    """
    POST the parsed items to ChainFlow's /inventory/sync/tally endpoint.

    Raises:
        requests.exceptions.ConnectionError — if ChainFlow API is unreachable.
        requests.exceptions.Timeout         — if API takes > 10 s.
        requests.exceptions.HTTPError       — if API returns 4xx/5xx.

    Returns the response JSON dict from ChainFlow, which matches
    TallySyncSummary: {"synced": N, "created": N, "errors": [...]}
    """
    url = f"{CHAINFLOW_API_URL}/inventory/sync/tally"
    payload = {"tenant_id": tenant_id, "items": items}

    logger.debug("Posting %d items to %s", len(items), url)
    response = requests.post(url, json=payload, timeout=_REQUEST_TIMEOUT)
    response.raise_for_status()

    result: dict = response.json()
    logger.info(
        "ChainFlow sync complete — synced=%s created=%s errors=%s",
        result.get("synced", "?"),
        result.get("created", "?"),
        len(result.get("errors", [])),
    )
    return result


def run_sync_cycle() -> None:
    """
    Orchestrate one complete Tally → ChainFlow sync cycle.

    This function is the `schedule` job target — it MUST never raise.
    All exceptions are caught, logged, and swallowed so the scheduler
    continues running and retries on the next 5-minute tick.

    Failure modes and their handling:
        Tally unreachable  → log ERROR "Tally connection failed", return
        Tally HTTP error   → log ERROR with status code, return
        Tally XML invalid  → _parse_tally_xml() returns [], we log WARNING
        No items from Tally → log WARNING, skip ChainFlow call (avoid no-op POST)
        ChainFlow unreachable → log ERROR "ChainFlow connection failed", return
        ChainFlow HTTP error  → log ERROR with status code, return
    """
    if not CHAINFLOW_TENANT_ID:
        logger.error(
            "CHAINFLOW_TENANT_ID not set in .env — cannot sync. "
            "Add CHAINFLOW_TENANT_ID=<your tenant id> to .env and restart."
        )
        return

    tenant_id = int(CHAINFLOW_TENANT_ID)

    # ── Step 1: fetch from Tally ─────────────────────────────────────────────
    try:
        items = fetch_stock_from_tally()
    except requests.exceptions.ConnectionError:
        logger.error(
            "Tally connection failed — is Tally Prime running and the HTTP "
            "server enabled on %s? Will retry next cycle.",
            TALLY_URL,
        )
        return
    except requests.exceptions.Timeout:
        logger.error("Tally request timed out after %ds. Will retry.", _REQUEST_TIMEOUT)
        return
    except requests.exceptions.HTTPError as exc:
        logger.error("Tally HTTP error: %s. Will retry.", exc)
        return

    if not items:
        logger.warning(
            "Tally returned 0 stock items — skipping ChainFlow sync. "
            "Check tally_sync.log at DEBUG level for the raw XML."
        )
        return

    # ── Step 2: push to ChainFlow ────────────────────────────────────────────
    try:
        sync_to_chainflow(items, tenant_id)
    except requests.exceptions.ConnectionError:
        logger.error(
            "ChainFlow API unreachable at %s — items NOT synced. Will retry.",
            CHAINFLOW_API_URL,
        )
        return
    except requests.exceptions.Timeout:
        logger.error(
            "ChainFlow API timed out after %ds — items NOT synced. Will retry.",
            _REQUEST_TIMEOUT,
        )
        return
    except requests.exceptions.HTTPError as exc:
        logger.error("ChainFlow API error: %s — items NOT synced. Will retry.", exc)
        return


# ──────────────────────────────────────────────────────────────────────────────
# Entry point (scheduler)
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info(
        "ChainFlow Tally Listener starting — "
        "Tally: %s | ChainFlow: %s | tenant_id: %s",
        TALLY_URL,
        CHAINFLOW_API_URL,
        CHAINFLOW_TENANT_ID or "NOT SET",
    )

    if not CHAINFLOW_TENANT_ID:
        logger.error(
            "CHAINFLOW_TENANT_ID is not set. "
            "Add it to .env before starting the listener."
        )
        raise SystemExit(1)

    # Run once immediately so the first data appears without waiting 5 minutes
    logger.info("Running initial sync cycle...")
    run_sync_cycle()

    # Schedule every 5 minutes thereafter
    schedule.every(5).minutes.do(run_sync_cycle)
    logger.info("Scheduler started — syncing every 5 minutes. Press Ctrl+C to stop.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Tally Listener stopped by user.")
