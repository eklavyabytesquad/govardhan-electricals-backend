"""Invoice numbering series per company (table: invoice_series)."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = "id,company_id,name,prefix,suffix,next_number,padding,is_default,created_at,updated_at"


def create(user_id, company_id, name, prefix="", suffix="", padding=4, is_default=False):
    require_manager(user_id, company_id)
    name = str(name or "").strip()
    if not name:
        raise ApiError(400, "Series name is required")
    try:
        padding = int(padding) if padding not in (None, "") else 4
    except (TypeError, ValueError):
        raise ApiError(400, "Invalid padding")

    if is_default:
        supabase.update("invoice_series", {"company_id": f"eq.{company_id}"}, {"is_default": False})

    payload = {
        "company_id": company_id, "name": name,
        "prefix": str(prefix or ""), "suffix": str(suffix or ""),
        "padding": padding, "is_default": bool(is_default),
        "created_by": user_id,
    }
    return supabase.insert("invoice_series", payload)


def list_for_company(user_id, company_id):
    require_member(user_id, company_id)
    return supabase.select("invoice_series", {"company_id": f"eq.{company_id}"}, COLS, order="created_at.asc")
