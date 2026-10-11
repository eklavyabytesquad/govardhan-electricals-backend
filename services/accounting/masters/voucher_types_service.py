"""Voucher types (table: voucher_types) — seeded automatically per company
(migration 010: Purchase, Sales, Payment, Receipt, Credit/Debit Note,
Journal, Contra, Stock Transfer, Opening Stock, Stock Adjustment, Purchase/
Sales Order, Receipt/Delivery Note). Only the numbering prefix is editable —
name/code/stock_effect/affects_accounts are structural, fixed at seed time."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = "id,company_id,name,code,stock_effect,affects_accounts,prefix,created_at,updated_at"


def list_for_company(user_id, company_id):
    require_member(user_id, company_id)
    return supabase.select("voucher_types", {"company_id": f"eq.{company_id}"}, COLS, order="name.asc")


def update_prefix(user_id, company_id, voucher_type_id, prefix):
    require_manager(user_id, company_id)
    voucher_type_id = str(voucher_type_id or "")
    if not voucher_type_id:
        raise ApiError(400, "id is required")
    rows = supabase.update(
        "voucher_types", {"id": f"eq.{voucher_type_id}", "company_id": f"eq.{company_id}"},
        {"prefix": str(prefix or ""), "updated_by": user_id},
    )
    if not rows:
        raise ApiError(404, "Voucher type not found")
    return rows[0]
