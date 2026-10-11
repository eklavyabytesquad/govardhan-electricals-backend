"""Ledgers (table: ledgers) — customers, suppliers, cash, bank, expenses,
every name money moves to or from. Ledgers with a system_role (Cash, Sales
A/c, the GST ledgers...) are seeded automatically per company; system_role
itself is never settable through the API."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = (
    "id,company_id,name,group_id,opening_balance,opening_type,gstin,pan,state_code,"
    "address,phone,email,credit_days,credit_limit,track_bills,system_role,is_active,"
    "created_at,updated_at"
)
FIELDS = (
    "name", "group_id", "opening_balance", "opening_type", "gstin", "pan", "state_code",
    "address", "phone", "email", "credit_days", "credit_limit", "track_bills", "is_active",
)


def _payload(data):
    return {f: data[f] for f in FIELDS if f in data}


def create(user_id, company_id, data):
    require_manager(user_id, company_id)
    name = str(data.get("name") or "").strip()
    if not name:
        raise ApiError(400, "Ledger name is required")
    if not data.get("group_id"):
        raise ApiError(400, "group_id is required")
    payload = {"company_id": company_id, "created_by": user_id, **_payload(data)}
    payload["name"] = name
    return supabase.insert("ledgers", payload)


def list_for_company(user_id, company_id):
    require_member(user_id, company_id)
    return supabase.select("ledgers", {"company_id": f"eq.{company_id}"}, COLS, order="name.asc")


def update(user_id, company_id, ledger_id, data):
    require_manager(user_id, company_id)
    ledger_id = str(ledger_id or "")
    if not ledger_id:
        raise ApiError(400, "id is required")
    payload = {"updated_by": user_id, **_payload(data)}
    if "name" in payload:
        payload["name"] = str(payload["name"]).strip()
        if not payload["name"]:
            raise ApiError(400, "Ledger name is required")
    rows = supabase.update("ledgers", {"id": f"eq.{ledger_id}", "company_id": f"eq.{company_id}"}, payload)
    if not rows:
        raise ApiError(404, "Ledger not found")
    return rows[0]
