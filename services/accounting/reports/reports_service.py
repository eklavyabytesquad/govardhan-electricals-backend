"""Read-only reports — every function here is a SELECT against the views
from migration 010 (v_stock_balance, v_stock_value, v_batch_expiry,
v_low_stock, v_ledger_balance, v_bill_outstanding, v_sales_profit). Nothing
is stored; everything is computed fresh on every call."""
import supabase
from errors import ApiError
from services.company_service import require_member


def stock_balance(user_id, company_id, item_id=None, godown_id=None):
    require_member(user_id, company_id)
    filters = {"company_id": f"eq.{company_id}"}
    if item_id:
        filters["item_id"] = f"eq.{item_id}"
    if godown_id:
        filters["godown_id"] = f"eq.{godown_id}"
    return supabase.select("v_stock_balance", filters)


def stock_value(user_id, company_id, item_id=None):
    require_member(user_id, company_id)
    filters = {"company_id": f"eq.{company_id}"}
    if item_id:
        filters["item_id"] = f"eq.{item_id}"
    return supabase.select("v_stock_value", filters)


def batch_expiry(user_id, company_id, item_id=None):
    require_member(user_id, company_id)
    filters = {"company_id": f"eq.{company_id}"}
    if item_id:
        filters["item_id"] = f"eq.{item_id}"
    return supabase.select("v_batch_expiry", filters, order="expiry_date.asc")


def low_stock(user_id, company_id):
    require_member(user_id, company_id)
    return supabase.select("v_low_stock", {"company_id": f"eq.{company_id}"})


def ledger_balance(user_id, company_id, ledger_id=None):
    require_member(user_id, company_id)
    filters = {"company_id": f"eq.{company_id}"}
    if ledger_id:
        filters["ledger_id"] = f"eq.{ledger_id}"
    return supabase.select("v_ledger_balance", filters)


def bill_outstanding(user_id, company_id, ledger_id=None):
    require_member(user_id, company_id)
    filters = {"company_id": f"eq.{company_id}"}
    if ledger_id:
        filters["ledger_id"] = f"eq.{ledger_id}"
    return supabase.select("v_bill_outstanding", filters, order="due_date.asc")


def sales_profit(user_id, company_id, voucher_id=None):
    require_member(user_id, company_id)
    filters = {"company_id": f"eq.{company_id}"}
    if voucher_id:
        filters["voucher_id"] = f"eq.{voucher_id}"
    return supabase.select("v_sales_profit", filters)


def ledger_transactions(user_id, company_id, ledger_id):
    """Every active voucher that touched one ledger, dated and in order —
    the Tally-style "ledger account" statement (migrations/012). The
    frontend adds the running balance on top of opening_balance/opening_type
    (fetched separately from /api/accounting/ledgers), since that's cheap
    to do client-side and keeps this view a flat, cacheable list."""
    require_member(user_id, company_id)
    ledger_id = str(ledger_id or "")
    if not ledger_id:
        raise ApiError(400, "ledger_id is required")
    return supabase.select(
        "v_ledger_transactions",
        {"company_id": f"eq.{company_id}", "ledger_id": f"eq.{ledger_id}"},
        order="date.asc,voucher_no.asc",
    )
