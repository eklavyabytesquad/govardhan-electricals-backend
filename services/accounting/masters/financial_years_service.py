"""Financial years (table: financial_years) — Indian FY, Apr 1 to Mar 31.

Creating one also creates a voucher_number_series row for every voucher
type in the company, the same thing migration 010's seed function does for
the first year, so every voucher type can auto-number from day one."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = "id,company_id,name,start_date,end_date,is_closed,created_at,updated_at"


def create(user_id, company_id, name, start_date, end_date):
    require_manager(user_id, company_id)
    name = str(name or "").strip()
    if not name:
        raise ApiError(400, "Financial year name is required, e.g. 2026-27")
    if not start_date or not end_date:
        raise ApiError(400, "start_date and end_date are required")

    fy = supabase.insert("financial_years", {
        "company_id": company_id, "name": name,
        "start_date": start_date, "end_date": end_date, "created_by": user_id,
    })

    voucher_types = supabase.select("voucher_types", {"company_id": f"eq.{company_id}"}, "id")
    if voucher_types:
        supabase.insert_many("voucher_number_series", [
            {
                "company_id": company_id, "voucher_type_id": vt["id"],
                "financial_year_id": fy["id"], "created_by": user_id,
            }
            for vt in voucher_types
        ])
    return fy


def list_for_company(user_id, company_id):
    require_member(user_id, company_id)
    return supabase.select("financial_years", {"company_id": f"eq.{company_id}"}, COLS, order="start_date.desc")


def close(user_id, company_id, financial_year_id):
    """Closes a financial year — vouchers in it can no longer be inserted,
    edited or cancelled (enforced by the DB triggers from migration 010).
    There's no re-open: closing is meant to be final once books are done."""
    require_manager(user_id, company_id)
    financial_year_id = str(financial_year_id or "")
    if not financial_year_id:
        raise ApiError(400, "id is required")
    rows = supabase.update(
        "financial_years", {"id": f"eq.{financial_year_id}", "company_id": f"eq.{company_id}"},
        {"is_closed": True, "updated_by": user_id},
    )
    if not rows:
        raise ApiError(404, "Financial year not found")
    return rows[0]
