"""
ChainFlow — integrations/notification_service.py
Azure Communication Services email notifications.

Called by routers/agents.py when a reorder recommendation is approved.
Sends an HTML email to the configured recipient with full recommendation
details and AI reasoning from Phi-4.

SMS is out of scope — phone-number provisioning via Azure Communication
Services requires a paid plan not covered by Azure for Students credits.
sms_sent is always set to False on recommendation records.

Environment variables (see .env.example):
    ACS_CONNECTION_STRING  — ACS resource connection string
    ACS_EMAIL_SENDER       — verified sender address (DoNotReply@xxx.azurecomm.net)
    TEST_EMAIL             — recipient email address

Never raises — notification failure must never block or roll back an approval.
"""

import logging
import os
import time

logger = logging.getLogger("chainflow.notifications")


def _send_with_retry(client, message: dict, max_attempts: int = 4) -> dict:
    """
    Send an ACS email with exponential backoff on TooManyRequests (429).
    Waits 2s, 4s, 8s between retries.  Raises on final failure.
    """
    delay = 2
    for attempt in range(1, max_attempts + 1):
        try:
            return client.begin_send(message).result()
        except Exception as exc:
            if attempt == max_attempts:
                raise
            if "TooManyRequests" in str(exc) or "429" in str(exc):
                logger.warning("ACS rate-limited, retrying in %ds (attempt %d/%d)", delay, attempt, max_attempts)
                time.sleep(delay)
                delay *= 2
            else:
                raise


def send_approval_email(
    sku_code: str,
    vendor_name: str,
    quantity: float,
    reasoning: str,
    recommendation_id: int,
) -> bool:
    """
    Send an HTML approval notification email via Azure Communication Services.

    Returns True on success, False on any failure (missing env vars, ACS error,
    network timeout, etc.).  Never raises — the caller must not be affected by
    a notification failure.
    """
    try:
        from azure.communication.email import EmailClient

        connection_string = os.getenv("ACS_CONNECTION_STRING")
        sender = os.getenv("ACS_EMAIL_SENDER")
        recipient = os.getenv("TEST_EMAIL")

        if connection_string is None or sender is None or recipient is None:
            logger.warning(
                "Email notification skipped — ACS_CONNECTION_STRING, "
                "ACS_EMAIL_SENDER, or TEST_EMAIL not configured in .env"
            )
            return False

        client = EmailClient.from_connection_string(connection_string)

        html_body = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
        <body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:40px 0;">
            <tr><td align="center">
              <table width="580" cellpadding="0" cellspacing="0"
                     style="background:#ffffff;border-radius:12px;overflow:hidden;
                            box-shadow:0 4px 24px rgba(0,0,0,0.08);">

                <!-- HEADER -->
                <tr>
                  <td style="background:#0f172a;padding:32px 40px;">
                    <p style="margin:0;font-size:11px;font-weight:600;letter-spacing:3px;
                               text-transform:uppercase;color:#94a3b8;">ChainFlow</p>
                    <h1 style="margin:8px 0 0;font-size:22px;font-weight:600;
                                color:#ffffff;letter-spacing:-0.3px;">
                      Purchase Order Approved
                    </h1>
                  </td>
                </tr>

                <!-- STATUS PILL -->
                <tr>
                  <td style="background:#f8fafc;padding:20px 40px;
                             border-bottom:1px solid #e2e8f0;">
                    <span style="display:inline-block;background:#dcfce7;color:#15803d;
                                  font-size:12px;font-weight:600;letter-spacing:1px;
                                  text-transform:uppercase;padding:4px 12px;
                                  border-radius:20px;">
                      Approved
                    </span>
                    <span style="margin-left:12px;color:#94a3b8;font-size:12px;">
                      Ref&nbsp;#{recommendation_id}
                    </span>
                  </td>
                </tr>

                <!-- DETAILS GRID -->
                <tr>
                  <td style="padding:32px 40px 24px;">
                    <table width="100%" cellpadding="0" cellspacing="0">
                      <tr>
                        <td width="50%" style="padding-bottom:20px;vertical-align:top;">
                          <p style="margin:0 0 4px;font-size:11px;font-weight:600;
                                     letter-spacing:1.5px;text-transform:uppercase;
                                     color:#94a3b8;">SKU</p>
                          <p style="margin:0;font-size:16px;font-weight:600;color:#0f172a;">
                            {sku_code}
                          </p>
                        </td>
                        <td width="50%" style="padding-bottom:20px;vertical-align:top;">
                          <p style="margin:0 0 4px;font-size:11px;font-weight:600;
                                     letter-spacing:1.5px;text-transform:uppercase;
                                     color:#94a3b8;">Vendor</p>
                          <p style="margin:0;font-size:16px;font-weight:600;color:#0f172a;">
                            {vendor_name}
                          </p>
                        </td>
                      </tr>
                      <tr>
                        <td width="50%" style="vertical-align:top;">
                          <p style="margin:0 0 4px;font-size:11px;font-weight:600;
                                     letter-spacing:1.5px;text-transform:uppercase;
                                     color:#94a3b8;">Quantity</p>
                          <p style="margin:0;font-size:16px;font-weight:600;color:#0f172a;">
                            {quantity:,.0f} <span style="font-size:13px;font-weight:400;
                                                         color:#64748b;">units</span>
                          </p>
                        </td>
                        <td width="50%" style="vertical-align:top;">
                          <p style="margin:0 0 4px;font-size:11px;font-weight:600;
                                     letter-spacing:1.5px;text-transform:uppercase;
                                     color:#94a3b8;">Status</p>
                          <p style="margin:0;font-size:16px;font-weight:600;color:#0f172a;">
                            Ready for PO
                          </p>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>

                <!-- DIVIDER -->
                <tr><td style="padding:0 40px;">
                  <hr style="border:none;border-top:1px solid #e2e8f0;margin:0;">
                </td></tr>

                <!-- REASONING -->
                <tr>
                  <td style="padding:24px 40px 32px;">
                    <p style="margin:0;font-size:14px;line-height:1.7;color:#334155;">
                      {reasoning}
                    </p>
                  </td>
                </tr>

                <!-- FOOTER -->
                <tr>
                  <td style="background:#f8fafc;padding:20px 40px;
                             border-top:1px solid #e2e8f0;">
                    <p style="margin:0;font-size:12px;color:#94a3b8;line-height:1.6;">
                      This is an automated notification from ChainFlow.<br>
                      Intelligent Supply Chain Copilot for Indian MSMEs.
                    </p>
                  </td>
                </tr>

              </table>
            </td></tr>
          </table>
        </body>
        </html>
        """

        message = {
            "senderAddress": sender,
            "recipients": {"to": [{"address": recipient}]},
            "content": {
                "subject": f"ChainFlow: Reorder Approved — {sku_code} from {vendor_name}",
                "html": html_body,
                "plainText": (
                    f"Reorder approved.\n"
                    f"SKU: {sku_code}\n"
                    f"Vendor: {vendor_name}\n"
                    f"Quantity: {quantity:,.0f}\n"
                    f"AI Reasoning: {reasoning}\n"
                    f"Reference: #{recommendation_id}"
                ),
            },
        }

        result = _send_with_retry(client, message)

        logger.info(
            "Approval email sent for recommendation #%d — status: %s",
            recommendation_id,
            result.get("status", "unknown"),
        )
        return True

    except Exception as exc:
        logger.error(
            "Email notification failed for recommendation #%d: %s",
            recommendation_id,
            exc,
        )
        return False


def send_rfq_confirmation_email(
    sku_code: str,
    vendor_count: int,
    quantity: float,
    unit: str,
    recommendation_id: int,
    reasoning: str | None = None,
    sku_name: str | None = None,
    meena_notified: bool = False,
) -> bool:
    """
    Combined approval + RFQ notification. Sent when Rohan clicks 'Place Order'.
    Includes AI reasoning and confirms RFQ has been dispatched to vendors.
    Never raises.
    """
    try:
        from azure.communication.email import EmailClient

        connection_string = os.getenv("ACS_CONNECTION_STRING")
        sender = os.getenv("ACS_EMAIL_SENDER")
        recipient = os.getenv("TEST_EMAIL")

        if not all([connection_string, sender, recipient]):
            logger.warning("send_rfq_confirmation_email: ACS env vars not set — skipping")
            return False

        assert connection_string is not None and sender is not None and recipient is not None
        client = EmailClient.from_connection_string(connection_string)

        sku_display = f"{sku_code}" + (f" — {sku_name}" if sku_name else "")
        reasoning_html = ""
        if reasoning:
            reasoning_html = f"""
<tr><td style="padding:8px 32px 24px">
  <div style="border-left:3px solid #1e3a5f;padding:12px 16px;background:#f4f6f9;border-radius:0 6px 6px 0">
    <div style="font-size:10px;text-transform:uppercase;letter-spacing:0.8px;color:#999;font-weight:600;margin-bottom:6px">AI Reasoning</div>
    <div style="font-size:13px;line-height:1.6;color:#334155">{reasoning}</div>
  </div>
</td></tr>"""

        meena_row = ""
        if meena_notified:
            meena_row = """
  <tr style="background:#ecfdf5">
    <td style="padding:9px 14px;font-size:12px;color:#065f46;border-bottom:1px solid #e2e6ea">
      Punjab Components House (Meena)
    </td>
    <td style="padding:9px 14px;font-size:12px;color:#065f46;text-align:right;border-bottom:1px solid #e2e6ea">
      Via Supplier Portal
    </td>
  </tr>"""

        html_body = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table width="580" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08)">

<tr><td style="background:#1e3a5f;padding:22px 32px">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td>
      <div style="color:#b0c8e8;font-size:10px;text-transform:uppercase;letter-spacing:1.2px;margin-bottom:4px">ChainFlow Procurement</div>
      <div style="color:#ffffff;font-size:18px;font-weight:700">Order Placed &mdash; RFQ Sent</div>
    </td>
    <td align="right">
      <div style="display:inline-block;background:#22c55e;color:#fff;font-size:10px;font-weight:700;
                  text-transform:uppercase;letter-spacing:1px;padding:4px 10px;border-radius:20px">Approved</div>
      <div style="color:#b0c8e8;font-size:11px;margin-top:4px">Ref #{recommendation_id}</div>
    </td>
  </tr></table>
</td></tr>

<tr><td style="padding:24px 32px 8px">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td width="50%" style="padding-bottom:16px;vertical-align:top">
        <div style="font-size:10px;color:#999;text-transform:uppercase;letter-spacing:0.8px">SKU</div>
        <div style="font-size:15px;font-weight:700;color:#1e3a5f;margin-top:3px">{sku_display}</div>
      </td>
      <td width="50%" style="padding-bottom:16px;vertical-align:top">
        <div style="font-size:10px;color:#999;text-transform:uppercase;letter-spacing:0.8px">Quantity</div>
        <div style="font-size:15px;font-weight:700;color:#333;margin-top:3px">{int(quantity):,} {unit}</div>
      </td>
    </tr>
    <tr>
      <td width="50%" style="vertical-align:top">
        <div style="font-size:10px;color:#999;text-transform:uppercase;letter-spacing:0.8px">Vendors Contacted</div>
        <div style="font-size:15px;font-weight:700;color:#333;margin-top:3px">{vendor_count}</div>
      </td>
      <td width="50%" style="vertical-align:top">
        <div style="font-size:10px;color:#999;text-transform:uppercase;letter-spacing:0.8px">Status</div>
        <div style="font-size:15px;font-weight:700;color:#d97706;margin-top:3px">Awaiting Quotes</div>
      </td>
    </tr>
  </table>
</td></tr>

{reasoning_html}

<tr><td style="padding:0 32px 24px">
  <div style="font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#999;margin-bottom:10px;font-weight:600">RFQ Dispatched To</div>
  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid #e2e6ea;border-radius:6px;overflow:hidden">
    <tr style="background:#1e3a5f">
      <th style="padding:9px 14px;text-align:left;color:#fff;font-size:10px;font-weight:600;text-transform:uppercase">Vendor</th>
      <th style="padding:9px 14px;text-align:right;color:#fff;font-size:10px;font-weight:600;text-transform:uppercase">Channel</th>
    </tr>
    {meena_row}
    <tr>
      <td colspan="2" style="padding:9px 14px;font-size:12px;color:#555;border-top:1px solid #e2e6ea">
        {vendor_count - (1 if meena_notified else 0)} additional vendor(s) notified via automated RFQ
      </td>
    </tr>
  </table>
</td></tr>

<tr><td style="padding:14px 32px;background:#f4f6f9;border-top:1px solid #e2e6ea">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td style="font-size:11px;color:#aaa">ChainFlow &middot; Intelligent Supply Chain Copilot</td>
    <td align="right" style="font-size:11px;color:#aaa">Ref #{recommendation_id}</td>
  </tr></table>
</td></tr>

</table></td></tr></table></body></html>"""

        message = {
            "senderAddress": sender,
            "recipients": {"to": [{"address": recipient}]},
            "content": {
                "subject": f"Order Placed — RFQ Sent for {sku_code} · Ref #{recommendation_id}",
                "html": html_body,
                "plainText": (
                    f"Order placed for {sku_code} ({int(quantity):,} {unit}). "
                    f"RFQ sent to {vendor_count} vendor(s). Ref #{recommendation_id}"
                ),
            },
        }
        result = _send_with_retry(client, message)
        logger.info("RFQ confirmation email sent for rec #%d — %s", recommendation_id, result.get("status"))
        return True

    except Exception as exc:
        logger.error("send_rfq_confirmation_email failed for #%d: %s", recommendation_id, exc)
        return False


def send_po_email(
    sku_code: str,
    vendor_name: str,
    quantity: float,
    unit: str,
    po_number: str,
    po_sas_url: str,
    order_value: float,
    recommendation_id: int,
) -> bool:
    """
    Notify the procurement team that a PO has been issued, with a PDF link.
    Never raises — notification failure must never block the workflow.
    """
    try:
        from azure.communication.email import EmailClient

        connection_string = os.getenv("ACS_CONNECTION_STRING")
        sender = os.getenv("ACS_EMAIL_SENDER")
        recipient = os.getenv("TEST_EMAIL")

        if not all([connection_string, sender, recipient]):
            logger.warning("send_po_email: ACS env vars not set — skipping")
            return False

        assert connection_string is not None and sender is not None and recipient is not None
        client = EmailClient.from_connection_string(connection_string)

        html_body = f"""
        <!DOCTYPE html><html lang="en"><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px">
          <table width="600" style="background:#fff;border-radius:8px;overflow:hidden;margin:auto">
            <tr><td style="background:#1e3a5f;padding:24px;text-align:center">
              <h1 style="color:#fff;margin:0;font-size:22px">Purchase Order Issued — ChainFlow</h1>
            </td></tr>
            <tr><td style="padding:28px">
              <table width="100%" style="border-collapse:collapse">
                <tr style="background:#f8f8f8"><td style="padding:10px;font-weight:bold">PO Number</td>
                  <td style="padding:10px">{po_number}</td></tr>
                <tr><td style="padding:10px;font-weight:bold">Vendor</td>
                  <td style="padding:10px">{vendor_name}</td></tr>
                <tr style="background:#f8f8f8"><td style="padding:10px;font-weight:bold">SKU</td>
                  <td style="padding:10px">{sku_code}</td></tr>
                <tr><td style="padding:10px;font-weight:bold">Quantity</td>
                  <td style="padding:10px">{quantity:,.0f} {unit}</td></tr>
                <tr style="background:#f8f8f8"><td style="padding:10px;font-weight:bold">Order Value</td>
                  <td style="padding:10px">₹ {order_value:,.2f}</td></tr>
              </table>
              <p style="margin-top:20px">
                <a href="{po_sas_url}" style="background:#1e3a5f;color:#fff;padding:10px 20px;border-radius:4px;text-decoration:none">
                  Download Purchase Order PDF
                </a>
              </p>
              <p style="font-size:12px;color:#aaa;margin-top:24px">
                ChainFlow · Intelligent Supply Chain Copilot
              </p>
            </td></tr>
          </table>
        </body></html>
        """

        message = {
            "senderAddress": sender,
            "recipients": {"to": [{"address": recipient}]},
            "content": {
                "subject": f"ChainFlow: PO Issued — {po_number} to {vendor_name}",
                "html": html_body,
                "plainText": (
                    f"PO {po_number} issued to {vendor_name} for {sku_code} "
                    f"({quantity:,.0f} {unit}). Order value: Rs {order_value:,.2f}. "
                    f"View PDF: {po_sas_url}"
                ),
            },
        }
        result = _send_with_retry(client, message)
        logger.info("PO email sent for rec #%d — %s", recommendation_id, result.get("status"))
        return True

    except Exception as exc:
        logger.error("send_po_email failed for rec #%d: %s", recommendation_id, exc)
        return False


def send_proforma_email(
    sku_code: str,
    vendor_name: str,
    quantity: float,
    unit: str,
    unit_price: float,
    lead_days: int,
    recommendation_id: int,
    proforma_sas_url: str | None = None,
) -> bool:
    """Proforma quote notification with itemised table, 2-month validity, and optional PDF download. Never raises."""
    try:
        from azure.communication.email import EmailClient
        connection_string = os.getenv("ACS_CONNECTION_STRING")
        sender = os.getenv("ACS_EMAIL_SENDER")
        recipient = os.getenv("TEST_EMAIL")
        if not all([connection_string, sender, recipient]):
            return False
        assert connection_string is not None and sender is not None and recipient is not None
        subtotal = round(quantity * unit_price, 2)
        client = EmailClient.from_connection_string(connection_string)
        html_body = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table width="580" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08)">
<tr><td style="background:#1e3a5f;padding:22px 32px">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td><div style="color:#b0c8e8;font-size:10px;text-transform:uppercase;letter-spacing:1.2px;margin-bottom:4px">ChainFlow Procurement</div>
        <div style="color:#ffffff;font-size:18px;font-weight:700">Proforma Invoice Received</div></td>
    <td align="right"><div style="color:#b0c8e8;font-size:11px">Ref #{recommendation_id}</div></td>
  </tr></table>
</td></tr>
<tr><td style="background:#f4f6f9;border-bottom:1px solid #e2e6ea;padding:14px 32px">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td><div style="font-size:10px;color:#999;text-transform:uppercase;letter-spacing:0.8px">From Vendor</div>
        <div style="font-size:15px;font-weight:600;color:#1e3a5f;margin-top:3px">{vendor_name}</div></td>
    <td align="right"><div style="font-size:10px;color:#999;text-transform:uppercase;letter-spacing:0.8px">Lead Time</div>
        <div style="font-size:15px;font-weight:600;color:#333;margin-top:3px">{lead_days} working days</div></td>
  </tr></table>
</td></tr>
<tr><td style="padding:28px 32px">
  <div style="font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#999;margin-bottom:10px;font-weight:600">Order Details</div>
  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border-radius:6px;overflow:hidden;border:1px solid #e2e6ea">
    <tr style="background:#1e3a5f">
      <th style="padding:10px 14px;text-align:left;color:#fff;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px">SKU</th>
      <th style="padding:10px 14px;text-align:right;color:#fff;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px">Quantity</th>
      <th style="padding:10px 14px;text-align:right;color:#fff;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px">Unit Price</th>
      <th style="padding:10px 14px;text-align:right;color:#fff;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px">Amount</th>
    </tr>
    <tr style="background:#f8f9fb">
      <td style="padding:13px 14px;font-size:13px;font-weight:600;color:#1e3a5f;border-bottom:1px solid #e2e6ea">{sku_code}</td>
      <td style="padding:13px 14px;font-size:13px;color:#444;text-align:right;border-bottom:1px solid #e2e6ea">{int(quantity):,} {unit}</td>
      <td style="padding:13px 14px;font-size:13px;color:#444;text-align:right;border-bottom:1px solid #e2e6ea">Rs {unit_price:,.2f}</td>
      <td style="padding:13px 14px;font-size:13px;font-weight:700;color:#1e3a5f;text-align:right;border-bottom:1px solid #e2e6ea">Rs {subtotal:,.2f}</td>
    </tr>
    <tr>
      <td colspan="3" style="padding:11px 14px;font-size:12px;font-weight:600;color:#555;text-align:right">Total (excl. GST)</td>
      <td style="padding:11px 14px;font-size:13px;font-weight:700;color:#1e3a5f;text-align:right;background:#eef2f7">Rs {subtotal:,.2f}</td>
    </tr>
  </table>
  <div style="margin-top:18px;padding:14px 18px;background:#f4f6f9;border-left:3px solid #1e3a5f;border-radius:0 4px 4px 0">
    <div style="font-size:12px;color:#333;line-height:1.6">
      <strong>Validity:</strong> This price is applicable for <strong>2 months</strong> from the date of this Proforma Invoice.<br>
      <strong>Payment Terms:</strong> 50% advance, 50% on delivery.<br>
      <strong>Lead Time:</strong> {lead_days} working days from receipt of confirmed Purchase Order.<br>
      <strong>Note:</strong> GST will be charged as applicable at the time of final invoice.
    </div>
  </div>
  {"" if not proforma_sas_url else f'''
  <div style="margin-top:16px;text-align:center">
    <a href="{proforma_sas_url}" style="display:inline-block;background:#1e3a5f;color:#ffffff;
       padding:10px 24px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600">
      Download Proforma Invoice PDF
    </a>
  </div>'''}
</td></tr>
<tr><td style="padding:14px 32px;background:#f4f6f9;border-top:1px solid #e2e6ea">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td style="font-size:11px;color:#aaa">ChainFlow · Intelligent Supply Chain Copilot</td>
    <td align="right" style="font-size:11px;color:#aaa">Ref #{recommendation_id}</td>
  </tr></table>
</td></tr>
</table></td></tr></table></body></html>"""
        message = {
            "senderAddress": sender,
            "recipients": {"to": [{"address": recipient}]},
            "content": {
                "subject": f"Proforma Received — {sku_code} from {vendor_name}",
                "html": html_body,
                "plainText": (
                    f"Proforma from {vendor_name} for {sku_code}: "
                    f"Rs {unit_price:,.2f}/{unit}, {int(quantity):,} {unit}, total Rs {subtotal:,.2f}. "
                    f"Valid 2 months. Lead {lead_days} days. Ref #{recommendation_id}"
                ),
            },
        }
        _send_with_retry(client, message)
        return True
    except Exception as exc:
        logger.error("send_proforma_email failed: %s", exc)
        return False


def send_invoice_email(
    sku_code: str,
    vendor_name: str,
    invoice_number: str,
    total_with_gst: float,
    recommendation_id: int,
    quantity: float | None = None,
    unit_price: float | None = None,
    unit: str | None = None,
    po_number: str | None = None,
) -> bool:
    """Tax invoice notification with itemised GST breakdown table. Never raises."""
    try:
        from azure.communication.email import EmailClient
        connection_string = os.getenv("ACS_CONNECTION_STRING")
        sender = os.getenv("ACS_EMAIL_SENDER")
        recipient = os.getenv("TEST_EMAIL")
        if not all([connection_string, sender, recipient]):
            return False
        assert connection_string is not None and sender is not None and recipient is not None
        # Pre-compute display values (handle optional params gracefully)
        po_ref     = po_number or "—"
        qty_str    = f"{int(quantity):,}" if quantity is not None else "—"
        unit_str   = unit or ""
        up_str     = f"Rs {unit_price:,.2f}" if unit_price is not None else "—"
        subtotal   = round(quantity * unit_price, 2) if (quantity and unit_price) else None
        sub_str    = f"Rs {subtotal:,.2f}" if subtotal is not None else "—"
        cgst       = round(subtotal * 0.09, 2) if subtotal is not None else None
        cgst_str   = f"Rs {cgst:,.2f}" if cgst is not None else "—"
        sgst_str   = cgst_str
        client = EmailClient.from_connection_string(connection_string)
        html_body = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table width="580" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08)">
<tr><td style="background:#1e3a5f;padding:22px 32px">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td><div style="color:#b0c8e8;font-size:10px;text-transform:uppercase;letter-spacing:1.2px;margin-bottom:4px">ChainFlow Procurement</div>
        <div style="color:#ffffff;font-size:18px;font-weight:700">Tax Invoice Received</div></td>
    <td align="right">
      <div style="color:#b0c8e8;font-size:11px">{invoice_number}</div>
      <div style="color:#b0c8e8;font-size:11px;margin-top:3px">Ref #{recommendation_id}</div>
    </td>
  </tr></table>
</td></tr>
<tr><td style="background:#f4f6f9;border-bottom:1px solid #e2e6ea;padding:14px 32px">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td><div style="font-size:10px;color:#999;text-transform:uppercase;letter-spacing:0.8px">From Vendor</div>
        <div style="font-size:15px;font-weight:600;color:#1e3a5f;margin-top:3px">{vendor_name}</div></td>
    <td align="right"><div style="font-size:10px;color:#999;text-transform:uppercase;letter-spacing:0.8px">PO Reference</div>
        <div style="font-size:15px;font-weight:600;color:#333;margin-top:3px">{po_ref}</div></td>
  </tr></table>
</td></tr>
<tr><td style="padding:28px 32px">
  <div style="font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#999;margin-bottom:10px;font-weight:600">Invoice Details</div>
  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border-radius:6px;overflow:hidden;border:1px solid #e2e6ea">
    <tr style="background:#1e3a5f">
      <th style="padding:10px 14px;text-align:left;color:#fff;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px">SKU</th>
      <th style="padding:10px 14px;text-align:right;color:#fff;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px">Quantity</th>
      <th style="padding:10px 14px;text-align:right;color:#fff;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px">Unit Price</th>
      <th style="padding:10px 14px;text-align:right;color:#fff;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px">Amount</th>
    </tr>
    <tr>
      <td style="padding:13px 14px;font-size:13px;font-weight:600;color:#1e3a5f;border-bottom:1px solid #e2e6ea">{sku_code}</td>
      <td style="padding:13px 14px;font-size:13px;color:#444;text-align:right;border-bottom:1px solid #e2e6ea">{qty_str} {unit_str}</td>
      <td style="padding:13px 14px;font-size:13px;color:#444;text-align:right;border-bottom:1px solid #e2e6ea">{up_str}</td>
      <td style="padding:13px 14px;font-size:13px;font-weight:700;color:#1e3a5f;text-align:right;border-bottom:1px solid #e2e6ea">{sub_str}</td>
    </tr>
    <tr>
      <td colspan="3" style="padding:9px 14px;font-size:12px;color:#666;text-align:right;border-bottom:1px solid #f0f0f0">CGST @ 9%</td>
      <td style="padding:9px 14px;font-size:12px;color:#444;text-align:right;border-bottom:1px solid #f0f0f0">{cgst_str}</td>
    </tr>
    <tr style="background:#f8f9fb">
      <td colspan="3" style="padding:9px 14px;font-size:12px;color:#666;text-align:right">SGST @ 9%</td>
      <td style="padding:9px 14px;font-size:12px;color:#444;text-align:right">{sgst_str}</td>
    </tr>
    <tr>
      <td colspan="3" style="padding:12px 14px;font-size:13px;font-weight:700;color:#fff;text-align:right;background:#1e3a5f">Total Payable (incl. GST)</td>
      <td style="padding:12px 14px;font-size:15px;font-weight:700;color:#ffffff;text-align:right;background:#1e3a5f">Rs {total_with_gst:,.2f}</td>
    </tr>
  </table>
  <div style="margin-top:18px;padding:14px 18px;background:#f4f6f9;border-left:3px solid #1e3a5f;border-radius:0 4px 4px 0">
    <div style="font-size:12px;color:#333;line-height:1.6">
      <strong>Payment Due:</strong> Within 30 days of invoice date.<br>
      <strong>Tax Breakup:</strong> CGST 9% + SGST 9% included in the total above.<br>
      Please reference <strong>{invoice_number}</strong> in all correspondence and remittances.
    </div>
  </div>
</td></tr>
<tr><td style="padding:14px 32px;background:#f4f6f9;border-top:1px solid #e2e6ea">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td style="font-size:11px;color:#aaa">ChainFlow · Intelligent Supply Chain Copilot</td>
    <td align="right" style="font-size:11px;color:#aaa">Ref #{recommendation_id}</td>
  </tr></table>
</td></tr>
</table></td></tr></table></body></html>"""
        message = {
            "senderAddress": sender,
            "recipients": {"to": [{"address": recipient}]},
            "content": {
                "subject": f"Tax Invoice Received — {invoice_number} from {vendor_name}",
                "html": html_body,
                "plainText": (
                    f"Tax Invoice {invoice_number} from {vendor_name} for {sku_code}. "
                    f"Total incl. GST: Rs {total_with_gst:,.2f}. "
                    f"Ref #{recommendation_id}"
                ),
            },
        }
        _send_with_retry(client, message)
        return True
    except Exception as exc:
        logger.error("send_invoice_email failed: %s", exc)
        return False


def send_spend_approval_request_email(
    approver_name: str,
    sku_code: str,
    sku_name: str,
    vendor_name: str,
    quantity: float,
    unit: str,
    order_value: float,
    po_number: str,
    recommendation_id: int,
) -> bool:
    """
    Notify Rohan or Harpreet that a high-value order is awaiting their spend approval.
    approver_name should be "Rohan" or "Harpreet" — capitalised for display.
    Never raises — notification failure must never block the workflow.
    """
    try:
        from azure.communication.email import EmailClient

        connection_string = os.getenv("ACS_CONNECTION_STRING")
        sender = os.getenv("ACS_EMAIL_SENDER")
        recipient = os.getenv("TEST_EMAIL")

        if not all([connection_string, sender, recipient]):
            logger.warning("send_spend_approval_request_email: ACS env vars not set — skipping")
            return False

        assert connection_string is not None and sender is not None and recipient is not None
        client = EmailClient.from_connection_string(connection_string)

        pill_color = "#dc2626" if approver_name.lower() == "harpreet" else "#d97706"
        pill_label = "High-Value Approval" if approver_name.lower() == "harpreet" else "Spend Approval"

        html_body = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table width="580" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08)">

<tr><td style="background:#1e3a5f;padding:22px 32px">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td>
      <div style="color:#b0c8e8;font-size:10px;text-transform:uppercase;letter-spacing:1.2px;margin-bottom:4px">ChainFlow Procurement</div>
      <div style="color:#ffffff;font-size:18px;font-weight:700">Spend Approval Required</div>
    </td>
    <td align="right">
      <div style="display:inline-block;background:{pill_color};color:#fff;font-size:10px;font-weight:700;
                  text-transform:uppercase;letter-spacing:1px;padding:4px 10px;border-radius:20px">{pill_label}</div>
      <div style="color:#b0c8e8;font-size:11px;margin-top:4px">Ref #{recommendation_id}</div>
    </td>
  </tr></table>
</td></tr>

<tr><td style="padding:24px 32px 8px">
  <p style="margin:0 0 20px;font-size:14px;color:#334155;line-height:1.6">
    Hi <strong>{approver_name}</strong>, a purchase order requires your approval before it can be issued.
  </p>
  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid #e2e6ea;border-radius:6px;overflow:hidden">
    <tr style="background:#1e3a5f">
      <th colspan="2" style="padding:9px 14px;text-align:left;color:#fff;font-size:10px;font-weight:600;text-transform:uppercase">Order Details</th>
    </tr>
    <tr style="background:#f8fafc">
      <td style="padding:10px 14px;font-size:12px;color:#64748b;font-weight:600;width:40%">PO Number</td>
      <td style="padding:10px 14px;font-size:13px;color:#0f172a;font-weight:700">{po_number}</td>
    </tr>
    <tr>
      <td style="padding:10px 14px;font-size:12px;color:#64748b;font-weight:600">SKU</td>
      <td style="padding:10px 14px;font-size:13px;color:#0f172a">{sku_code} — {sku_name}</td>
    </tr>
    <tr style="background:#f8fafc">
      <td style="padding:10px 14px;font-size:12px;color:#64748b;font-weight:600">Vendor</td>
      <td style="padding:10px 14px;font-size:13px;color:#0f172a">{vendor_name}</td>
    </tr>
    <tr>
      <td style="padding:10px 14px;font-size:12px;color:#64748b;font-weight:600">Quantity</td>
      <td style="padding:10px 14px;font-size:13px;color:#0f172a">{int(quantity):,} {unit}</td>
    </tr>
    <tr style="background:#f8fafc">
      <td style="padding:10px 14px;font-size:12px;color:#64748b;font-weight:600">Order Value</td>
      <td style="padding:10px 14px;font-size:15px;color:#0f172a;font-weight:700">&#x20b9; {order_value:,.2f}</td>
    </tr>
  </table>
</td></tr>

<tr><td style="padding:20px 32px 28px">
  <p style="margin:0 0 12px;font-size:13px;color:#64748b;line-height:1.6">
    Log in to the ChainFlow dashboard to approve or reject this order.
  </p>
</td></tr>

<tr><td style="padding:14px 32px;background:#f4f6f9;border-top:1px solid #e2e6ea">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td style="font-size:11px;color:#aaa">ChainFlow &middot; Intelligent Supply Chain Copilot</td>
    <td align="right" style="font-size:11px;color:#aaa">Ref #{recommendation_id}</td>
  </tr></table>
</td></tr>

</table></td></tr></table></body></html>"""

        message = {
            "senderAddress": sender,
            "recipients": {"to": [{"address": recipient}]},
            "content": {
                "subject": (
                    f"Action Required: {pill_label} — {sku_code} Rs {order_value:,.0f} · Ref #{recommendation_id}"
                ),
                "html": html_body,
                "plainText": (
                    f"Hi {approver_name}, spend approval needed for {sku_code} ({sku_name}). "
                    f"PO {po_number} to {vendor_name}, {int(quantity):,} {unit}, "
                    f"order value Rs {order_value:,.2f}. Log in to ChainFlow to approve or reject."
                ),
            },
        }
        result = _send_with_retry(client, message)
        logger.info(
            "Spend approval request email sent to %s for rec #%d — %s",
            approver_name, recommendation_id, result.get("status"),
        )
        return True

    except Exception as exc:
        logger.error("send_spend_approval_request_email failed for rec #%d: %s", recommendation_id, exc)
        return False
