"""One invoice_config row per company: template, permanent/legal details,
GSTIN, address, bank/account details (table: invoice_config)."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = (
    "id,company_id,template_name,logo_url,theme_color,notes,legal_name,pan,cin,"
    "gstin,gstin_state,gstin_state_code,address_line1,address_line2,city,state,pincode,country,"
    "bank_name,bank_account_name,bank_account_number,bank_ifsc,bank_branch,upi_id,"
    "default_series_id,created_at,updated_at"
)

FIELDS = [
    "template_name", "logo_url", "theme_color", "notes", "legal_name", "pan", "cin",
    "gstin", "gstin_state", "gstin_state_code", "address_line1", "address_line2",
    "city", "state", "pincode", "country",
    "bank_name", "bank_account_name", "bank_account_number", "bank_ifsc", "bank_branch", "upi_id",
    "default_series_id",
]


def get(user_id, company_id):
    require_member(user_id, company_id)
    rows = supabase.select("invoice_config", {"company_id": f"eq.{company_id}"}, COLS, 1)
    return rows[0] if rows else None


def upsert(user_id, company_id, data):
    require_manager(user_id, company_id)
    payload = {k: (str(v).strip() or None if isinstance(v, str) else v) for k, v in data.items() if k in FIELDS}

    existing = supabase.select("invoice_config", {"company_id": f"eq.{company_id}"}, "id", 1)
    if existing:
        payload["updated_by"] = user_id
        rows = supabase.update("invoice_config", {"id": f"eq.{existing[0]['id']}"}, payload)
        return rows[0]

    payload.update({"company_id": company_id, "created_by": user_id})
    return supabase.insert("invoice_config", payload)
