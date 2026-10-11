"""Read-only: per voucher-type, per-financial-year running numbers (table:
voucher_number_series). Rows are created automatically — by the company
seed (migration 010) for the first financial year, and by
financial_years_service.create() for every year after that. There is no
write endpoint: the number is only ever claimed atomically inside
save_voucher() (migration 011), never set directly."""
import supabase
from services.company_service import require_member

COLS = "id,company_id,voucher_type_id,financial_year_id,next_number,created_at,updated_at"


def list_for_company(user_id, company_id, financial_year_id=None):
    require_member(user_id, company_id)
    filters = {"company_id": f"eq.{company_id}"}
    if financial_year_id:
        filters["financial_year_id"] = f"eq.{financial_year_id}"
    return supabase.select("voucher_number_series", filters, COLS)
