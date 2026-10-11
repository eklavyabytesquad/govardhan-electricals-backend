"""Accounting base units (table: units) — pcs, kg, ltr... with decimal
places for quantity rounding. NOT the old per-invoice unit picklist (that
table was dropped in migrations/009_remove_invoicing.sql)."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = "id,company_id,name,decimal_places,created_at,updated_at"


def create(user_id, company_id, name, decimal_places=0):
    require_manager(user_id, company_id)
    name = str(name or "").strip()
    if not name:
        raise ApiError(400, "Unit name is required")
    return supabase.insert("units", {
        "company_id": company_id, "name": name,
        "decimal_places": int(decimal_places or 0), "created_by": user_id,
    })


def list_for_company(user_id, company_id):
    require_member(user_id, company_id)
    return supabase.select("units", {"company_id": f"eq.{company_id}"}, COLS, order="name.asc")


def update(user_id, company_id, unit_id, name=None, decimal_places=None):
    require_manager(user_id, company_id)
    unit_id = str(unit_id or "")
    if not unit_id:
        raise ApiError(400, "id is required")
    payload = {"updated_by": user_id}
    if name is not None:
        name = str(name).strip()
        if not name:
            raise ApiError(400, "Unit name is required")
        payload["name"] = name
    if decimal_places is not None:
        payload["decimal_places"] = int(decimal_places)
    rows = supabase.update("units", {"id": f"eq.{unit_id}", "company_id": f"eq.{company_id}"}, payload)
    if not rows:
        raise ApiError(404, "Unit not found")
    return rows[0]
