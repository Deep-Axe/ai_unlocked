"""
ChainFlow — integrations/po_generator.py
PDF generation for procurement documents using reportlab.

Generates three document types:
  - Proforma Invoice (vendor -> buyer, price quote)
  - Purchase Order   (buyer -> vendor, confirmed order)
  - Tax Invoice      (vendor -> buyer, final invoice with GST)

All functions return bytes ready to pass to blob_storage.upload_document().
"""

import io
from datetime import datetime, timedelta

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
)


# ── Shared style helpers ──────────────────────────────────────────────────────

def _base_doc(buffer):
    return SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )

def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle("DocTitle",
        fontName="Helvetica-Bold", fontSize=20, spaceAfter=4,
        textColor=colors.HexColor("#1e3a5f")))
    s.add(ParagraphStyle("DocSubtitle",
        fontName="Helvetica", fontSize=10, spaceAfter=2,
        textColor=colors.HexColor("#555555")))
    s.add(ParagraphStyle("SectionHead",
        fontName="Helvetica-Bold", fontSize=9, spaceAfter=2,
        textColor=colors.HexColor("#1e3a5f")))
    s.add(ParagraphStyle("Body",
        fontName="Helvetica", fontSize=9, spaceAfter=2))
    s.add(ParagraphStyle("Footer",
        fontName="Helvetica-Oblique", fontSize=7,
        textColor=colors.HexColor("#888888")))
    s.add(ParagraphStyle("VendorName",
        fontName="Helvetica-Bold", fontSize=16, spaceAfter=0,
        leading=22, textColor=colors.HexColor("#1e3a5f")))
    s.add(ParagraphStyle("InvoiceRef",
        fontName="Helvetica-Bold", fontSize=14, spaceAfter=0,
        leading=22, alignment=2, textColor=colors.HexColor("#1e3a5f")))
    return s

def _address_block(s, label, name, city, extra=""):
    return [
        Paragraph(label, s["SectionHead"]),
        Paragraph(name, s["Body"]),
        Paragraph(city, s["Body"]),
        Paragraph(extra, s["Body"]) if extra else Spacer(1, 2),
        Spacer(1, 6),
    ]

def _items_table(sku_code, description, quantity, unit, unit_price):
    total = round(quantity * unit_price, 2)
    data = [
        ["#", "SKU Code", "Description", "Qty", "Unit", "Unit Price (Rs)", "Total (Rs)"],
        ["1", sku_code, description, str(int(quantity)), unit,
         f"{unit_price:,.2f}", f"{total:,.2f}"],
        ["", "", "", "", "", "Subtotal", f"{total:,.2f}"],
    ]
    style = TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1e3a5f")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 8),
        ("GRID",       (0,0), (-1,-1), 0.5, colors.HexColor("#cccccc")),
        ("ALIGN",      (3,0), (-1,-1), "RIGHT"),
        ("FONTNAME",   (5,2), (-1,2), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1),
         [colors.HexColor("#f9f9f9"), colors.white]),
    ])
    return Table(data,
        colWidths=[0.6*cm, 2.5*cm, 5.5*cm, 1.5*cm, 1.5*cm, 2.5*cm, 2.5*cm],
        style=style)


# ── Document generators ───────────────────────────────────────────────────────

def generate_proforma_pdf(
    rec_id: int,
    sku_code: str,
    vendor_name: str,
    quantity: float,
    unit_price: float,
    unit: str,
    lead_time_days: int,
    sku_description: str = "Supply of materials",
    tenant_name: str = "Harpreet Hosiery Works",
) -> bytes:
    """Proforma Invoice — issued by vendor to buyer as a price quote."""
    buffer = io.BytesIO()
    doc = _base_doc(buffer)
    s = _styles()
    now = datetime.utcnow()
    proforma_num = f"PRO-{now.year}-{rec_id:04d}"
    total = round(quantity * unit_price, 2)

    story = [
        Paragraph("PROFORMA INVOICE", s["DocTitle"]),
        Paragraph("Subject to final confirmation", s["DocSubtitle"]),
        HRFlowable(width="100%", thickness=1,
                   color=colors.HexColor("#1e3a5f"), spaceAfter=8),
        Table([
            ["Proforma No.", proforma_num, "Date", now.strftime("%d/%m/%Y")],
            ["Valid Until", (now + timedelta(days=7)).strftime("%d/%m/%Y"),
             "RFQ Ref", f"RFQ-{rec_id:04d}"],
        ], colWidths=[3*cm, 5*cm, 3*cm, 5*cm],
        style=TableStyle([
            ("FONTSIZE",  (0,0), (-1,-1), 8),
            ("FONTNAME",  (0,0), (0,-1), "Helvetica-Bold"),
            ("FONTNAME",  (2,0), (2,-1), "Helvetica-Bold"),
            ("GRID",      (0,0), (-1,-1), 0.3, colors.HexColor("#eeeeee")),
            ("BACKGROUND",(0,0), (-1,-1), colors.HexColor("#f5f8ff")),
        ])),
        Spacer(1, 12),
        *_address_block(s, "FROM (Vendor)", vendor_name,
                        "India", f"Lead Time: {lead_time_days} working days"),
        *_address_block(s, "TO (Buyer)", tenant_name, "Ludhiana, Punjab",
                        "GSTIN: 03AABHH1234D1Z7"),
        Spacer(1, 8),
        _items_table(sku_code, sku_description, quantity, unit, unit_price),
        Spacer(1, 16),
        Table([
            ["Payment Terms:", "50% advance, 50% on delivery"],
            ["Delivery Terms:", "Ex-works vendor location"],
            ["Quote Valid For:", "7 days from date of issue"],
        ], colWidths=[4*cm, 12*cm],
        style=TableStyle([
            ("FONTSIZE", (0,0), (-1,-1), 8),
            ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
        ])),
        Spacer(1, 24),
        HRFlowable(width="100%", thickness=0.5,
                   color=colors.HexColor("#cccccc"), spaceAfter=4),
        Paragraph(
            f"Generated by ChainFlow Procurement System | "
            f"Total Value: Rs {total:,.2f} | {now.strftime('%d %b %Y %H:%M')} UTC",
            s["Footer"]
        ),
    ]
    doc.build(story)
    return buffer.getvalue()


def generate_purchase_order_pdf(
    rec_id: int,
    sku_code: str,
    vendor_name: str,
    quantity: float,
    unit_price: float,
    unit: str,
    sku_description: str = "Supply of materials",
    tenant_name: str = "Harpreet Hosiery Works",
) -> bytes:
    """Purchase Order — issued by buyer to winning vendor."""
    buffer = io.BytesIO()
    doc = _base_doc(buffer)
    s = _styles()
    now = datetime.utcnow()
    po_number = f"PO-{now.year}-{rec_id:04d}"
    total = round(quantity * unit_price, 2)

    story = [
        Paragraph("PURCHASE ORDER", s["DocTitle"]),
        Paragraph(f"PO Number: {po_number}", s["DocSubtitle"]),
        HRFlowable(width="100%", thickness=1,
                   color=colors.HexColor("#1e3a5f"), spaceAfter=8),
        Table([
            ["PO Number", po_number, "Date", now.strftime("%d/%m/%Y")],
            ["Delivery Required By", (now + timedelta(days=10)).strftime("%d/%m/%Y"),
             "Payment Terms", "Net 30 days"],
        ], colWidths=[3*cm, 5*cm, 3*cm, 5*cm],
        style=TableStyle([
            ("FONTSIZE",  (0,0), (-1,-1), 8),
            ("FONTNAME",  (0,0), (0,-1), "Helvetica-Bold"),
            ("FONTNAME",  (2,0), (2,-1), "Helvetica-Bold"),
            ("GRID",      (0,0), (-1,-1), 0.3, colors.HexColor("#eeeeee")),
            ("BACKGROUND",(0,0), (-1,-1), colors.HexColor("#f5f8ff")),
        ])),
        Spacer(1, 12),
        *_address_block(s, "FROM (Buyer)", tenant_name,
                        "Model Town, Ludhiana, Punjab — 141002",
                        "GSTIN: 03AABHH1234D1Z7"),
        *_address_block(s, "TO (Vendor)", vendor_name, "India",
                        "Please quote this PO number on all correspondence."),
        Spacer(1, 8),
        _items_table(sku_code, sku_description, quantity, unit, unit_price),
        Spacer(1, 16),
        Paragraph("Terms & Conditions", s["SectionHead"]),
        Paragraph(
            "1. Delivery must be completed by the date specified above. "
            "2. All goods must meet quality specifications agreed at time of RFQ. "
            "3. Tax Invoice must be submitted within 3 days of delivery. "
            "4. This PO is issued subject to satisfactory delivery.",
            s["Body"]
        ),
        Spacer(1, 24),
        Table([
            ["Authorised By:", "Harpreet Singh"],
            ["Designation:", "Owner, Harpreet Hosiery Works"],
            ["System:", "ChainFlow AI Procurement"],
        ], colWidths=[4*cm, 12*cm],
        style=TableStyle([
            ("FONTSIZE", (0,0), (-1,-1), 8),
            ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
        ])),
        Spacer(1, 12),
        HRFlowable(width="100%", thickness=0.5,
                   color=colors.HexColor("#cccccc"), spaceAfter=4),
        Paragraph(
            f"Generated by ChainFlow v1.0 | PO Value: Rs {total:,.2f} | "
            f"{now.strftime('%d %b %Y %H:%M')} UTC",
            s["Footer"]
        ),
    ]
    doc.build(story)
    return buffer.getvalue()


def generate_tax_invoice_pdf(
    rec_id: int,
    sku_code: str,
    vendor_name: str,
    quantity: float,
    unit_price: float,
    unit: str,
    gst_rate: float = 0.18,
    sku_description: str = "Supply of materials",
    tenant_name: str = "Harpreet Hosiery Works",
    invoice_number: str | None = None,
    po_number: str | None = None,
) -> bytes:
    """Tax Invoice — issued by vendor, with vendor letterhead and GST breakdown."""
    buffer = io.BytesIO()
    doc = _base_doc(buffer)
    s = _styles()
    now = datetime.utcnow()
    invoice_number = invoice_number or f"INV-{now.year}-{rec_id:04d}"
    po_ref = po_number or f"PO-{now.year}-{rec_id:04d}"
    subtotal = round(quantity * unit_price, 2)
    cgst = round(subtotal * 0.09, 2)
    sgst = round(subtotal * 0.09, 2)
    total = round(subtotal + cgst + sgst, 2)

    # ── Vendor letterhead ─────────────────────────────────────────────────────
    letterhead = Table([[
        Paragraph(
            f"{vendor_name}<br/>"
            f"<font name='Helvetica' size='8' color='#777777'>"
            f"Authorized Supplier  ·  India  ·  GSTIN: [Registered]</font>",
            s["VendorName"],
        ),
        Paragraph(
            f"TAX INVOICE<br/>"
            f"<font name='Helvetica' size='9' color='#555555'>"
            f"Invoice No: {invoice_number}</font><br/>"
            f"<font name='Helvetica' size='9' color='#555555'>"
            f"Date: {now.strftime('%d %b %Y')}</font>",
            s["InvoiceRef"],
        ),
    ]], colWidths=[10*cm, 6*cm], style=TableStyle([
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("BACKGROUND",    (0,0), (-1,-1), colors.HexColor("#eef2f7")),
        ("TOPPADDING",    (0,0), (-1,-1), 14),
        ("BOTTOMPADDING", (0,0), (-1,-1), 14),
        ("LEFTPADDING",   (0,0), (0,-1),  14),
        ("RIGHTPADDING",  (-1,0), (-1,-1), 14),
    ]))

    story = [
        letterhead,
        HRFlowable(width="100%", thickness=2,
                   color=colors.HexColor("#1e3a5f"), spaceAfter=12),
        Table([
            ["PO Reference",    po_ref,        "Invoice Date",
             now.strftime("%d/%m/%Y")],
            ["Place of Supply", "Punjab (03)", "Payment Due",
             (now + timedelta(days=30)).strftime("%d/%m/%Y")],
        ], colWidths=[3*cm, 5*cm, 3*cm, 5*cm],
        style=TableStyle([
            ("FONTSIZE",   (0,0), (-1,-1), 8),
            ("FONTNAME",   (0,0), (0,-1),  "Helvetica-Bold"),
            ("FONTNAME",   (2,0), (2,-1),  "Helvetica-Bold"),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#eeeeee")),
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f5f8ff")),
        ])),
        Spacer(1, 12),
        *_address_block(s, "FROM (Supplier)", vendor_name, "India",
                        "GSTIN: [Vendor GSTIN]"),
        *_address_block(s, "TO (Recipient)", tenant_name,
                        "Model Town, Ludhiana, Punjab — 141002",
                        "GSTIN: 03AABHH1234D1Z7"),
        Spacer(1, 8),
        _items_table(sku_code, sku_description, quantity, unit, unit_price),
        Spacer(1, 8),
        Table([
            ["", "", "", "", "", "Subtotal",  f"Rs {subtotal:,.2f}"],
            ["", "", "", "", "", "CGST @ 9%", f"Rs {cgst:,.2f}"],
            ["", "", "", "", "", "SGST @ 9%", f"Rs {sgst:,.2f}"],
            ["", "", "", "", "", "TOTAL",     f"Rs {total:,.2f}"],
        ], colWidths=[0.6*cm, 2.5*cm, 5.5*cm, 1.5*cm, 1.5*cm, 2.5*cm, 2.5*cm],
        style=TableStyle([
            ("FONTSIZE",   (0,0), (-1,-1), 8),
            ("FONTNAME",   (5,-1), (-1,-1), "Helvetica-Bold"),
            ("FONTSIZE",   (5,-1), (-1,-1), 9),
            ("BACKGROUND", (5,-1), (-1,-1), colors.HexColor("#1e3a5f")),
            ("TEXTCOLOR",  (5,-1), (-1,-1), colors.white),
            ("ALIGN",      (5,0),  (-1,-1), "RIGHT"),
        ])),
        Spacer(1, 24),
        Paragraph("Declaration", s["SectionHead"]),
        Paragraph(
            "We declare that this invoice shows the actual price of the goods "
            "described and that all particulars are true and correct.",
            s["Body"]
        ),
        Spacer(1, 12),
        HRFlowable(width="100%", thickness=0.5,
                   color=colors.HexColor("#cccccc"), spaceAfter=4),
        Paragraph(
            f"ChainFlow AI Procurement System  |  "
            f"Invoice Total (incl. GST): Rs {total:,.2f}  |  "
            f"{now.strftime('%d %b %Y %H:%M')} UTC",
            s["Footer"]
        ),
    ]
    doc.build(story)
    return buffer.getvalue()
