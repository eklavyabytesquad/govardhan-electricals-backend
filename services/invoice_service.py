"""Invoices + line items (tables: invoices, invoice_items).

Numbering: pass invoice_number for a manual number, or series_id to have
next_invoice_number() (a Postgres function, see migrations/004_invoicing.sql)
atomically claim the next number in that series. Exactly one of the two is
required.
"""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

INVOICE_COLS = (
    "id,company_id,series_id,invoice_number,is_manual_number,invoice_date,due_date,status,"
    "customer_name,customer_gstin,customer_phone,customer_email,customer_address,"
    "currency,subtotal,discount_total,tax_total,grand_total,amount_paid,notes,terms,metadata,"
    "created_by,updated_by,created_at,updated_at"
)
ITEM_COLS = (
    "id,invoice_id,inventory_id,description,hsn_code,quantity,unit,unit_price,"
    "discount_percent,tax_rate,tax_amount,line_total,sort_order"
)
STATUSES = ("draft", "sent", "paid", "partially_paid", "overdue", "cancelled")


def _num(value, default=0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ApiError(400, "Invalid number")


def _compute_items(items):
    """Validates line items and computes each one's tax_amount/line_total, plus invoice totals."""
    if not items:
        raise ApiError(400, "At least one line item is required")
    cleaned, subtotal, tax_total, discount_total = [], 0.0, 0.0, 0.0
    for i, it in enumerate(items):
        description = str((it or {}).get("description") or "").strip()
        if not description:
            raise ApiError(400, f"Line item {i + 1}: description is required")
        qty = _num(it.get("quantity"), 1)
        unit_price = _num(it.get("unit_price"))
        discount_percent = _num(it.get("discount_percent"))
        tax_rate = _num(it.get("tax_rate"))

        gross = qty * unit_price
        discount_amount = gross * (discount_percent / 100)
        taxable = gross - discount_amount
        tax_amount = taxable * (tax_rate / 100)
        line_total = taxable + tax_amount

        subtotal += gross
        discount_total += discount_amount
        tax_total += tax_amount

        cleaned.append({
            "inventory_id": it.get("inventory_id") or None,
            "description": description,
            "hsn_code": str(it.get("hsn_code") or "").strip() or None,
            "quantity": qty,
            "unit": str(it.get("unit") or "Nos"),
            "unit_price": unit_price,
            "discount_percent": discount_percent,
            "tax_rate": tax_rate,
            "tax_amount": round(tax_amount, 2),
            "line_total": round(line_total, 2),
            "sort_order": i,
        })

    totals = {
        "subtotal": round(subtotal, 2),
        "discount_total": round(discount_total, 2),
        "tax_total": round(tax_total, 2),
        "grand_total": round(subtotal - discount_total + tax_total, 2),
    }
    return cleaned, totals


def create(user_id, company_id, data):
    require_manager(user_id, company_id)

    customer_name = str(data.get("customer_name") or "").strip()
    if not customer_name:
        raise ApiError(400, "Customer name is required")

    status = data.get("status") or "draft"
    if status not in STATUSES:
        raise ApiError(400, "Invalid status")

    series_id = data.get("series_id") or None
    manual_number = str(data.get("invoice_number") or "").strip()
    if manual_number:
        invoice_number, is_manual = manual_number, True
    elif series_id:
        invoice_number, is_manual = supabase.rpc("next_invoice_number", {"p_series_id": series_id}), False
    else:
        raise ApiError(400, "Provide either invoice_number (manual) or series_id (auto-numbered)")

    items, totals = _compute_items(data.get("items"))

    payload = {
        "company_id": company_id,
        "series_id": series_id,
        "invoice_number": invoice_number,
        "is_manual_number": is_manual,
        "due_date": data.get("due_date") or None,
        "status": status,
        "customer_name": customer_name,
        "customer_gstin": str(data.get("customer_gstin") or "").strip() or None,
        "customer_phone": str(data.get("customer_phone") or "").strip() or None,
        "customer_email": str(data.get("customer_email") or "").strip() or None,
        "customer_address": str(data.get("customer_address") or "").strip() or None,
        "currency": data.get("currency") or "INR",
        "notes": str(data.get("notes") or "").strip() or None,
        "terms": str(data.get("terms") or "").strip() or None,
        "created_by": user_id,
        **totals,
    }
    if data.get("invoice_date"):  # omit rather than send null, so the column's default (today) applies
        payload["invoice_date"] = data.get("invoice_date")
    invoice = supabase.insert("invoices", payload)

    for it in items:
        it["invoice_id"] = invoice["id"]
    saved_items = supabase.insert_many("invoice_items", items)

    return {**invoice, "items": saved_items}


def list_for_company(user_id, company_id, status=None):
    require_member(user_id, company_id)
    filters = {"company_id": f"eq.{company_id}"}
    if status:
        filters["status"] = f"eq.{status}"
    return supabase.select("invoices", filters, INVOICE_COLS, order="created_at.desc")


def get_one(user_id, company_id, invoice_id):
    require_member(user_id, company_id)
    invoice_id = str(invoice_id or "")
    rows = supabase.select("invoices", {"id": f"eq.{invoice_id}", "company_id": f"eq.{company_id}"}, INVOICE_COLS, 1)
    if not rows:
        raise ApiError(404, "Invoice not found")
    items = supabase.select("invoice_items", {"invoice_id": f"eq.{invoice_id}"}, ITEM_COLS, order="sort_order.asc")
    return {**rows[0], "items": items}


def update_status(user_id, company_id, invoice_id, status, amount_paid=None):
    require_manager(user_id, company_id)
    if status not in STATUSES:
        raise ApiError(400, "Invalid status")
    payload = {"status": status, "updated_by": user_id}
    if amount_paid is not None:
        payload["amount_paid"] = _num(amount_paid)
    rows = supabase.update("invoices", {"id": f"eq.{invoice_id}", "company_id": f"eq.{company_id}"}, payload)
    if not rows:
        raise ApiError(404, "Invoice not found")
    return rows[0]
