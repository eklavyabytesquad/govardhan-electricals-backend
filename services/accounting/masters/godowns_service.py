"""Warehouses / stock locations (table: godowns). Only one default per
company — enforced at the DB level by a partial unique index; setting a new
default clears the old one first so that index is never tripped."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = "id,company_id,name,address,is_default,created_at,updated_at"


def _clear_existing_default(company_id, user_id):
    supabase.update(
        "godowns", {"company_id": f"eq.{company_id}", "is_default": "eq.true"},
        {"is_default": False, "updated_by": user_id},
    )


def create(user_id, company_id, name, address=None, is_default=False):
    require_manager(user_id, company_id)
    name = str(name or "").strip()
    if not name:
        raise ApiError(400, "Godown name is required")
    if is_default:
        _clear_existing_default(company_id, user_id)
    return supabase.insert("godowns", {
        "company_id": company_id, "name": name, "address": address or None,
        "is_default": bool(is_default), "created_by": user_id,
    })


def list_for_company(user_id, company_id):
    require_member(user_id, company_id)
    return supabase.select("godowns", {"company_id": f"eq.{company_id}"}, COLS, order="name.asc")


def update(user_id, company_id, godown_id, name=None, address=None, is_default=None):
    require_manager(user_id, company_id)
    godown_id = str(godown_id or "")
    if not godown_id:
        raise ApiError(400, "id is required")
    if is_default:
        _clear_existing_default(company_id, user_id)
    payload = {"updated_by": user_id}
    if name is not None:
        name = str(name).strip()
        if not name:
            raise ApiError(400, "Godown name is required")
        payload["name"] = name
    if address is not None:
        payload["address"] = address or None
    if is_default is not None:
        payload["is_default"] = bool(is_default)
    rows = supabase.update("godowns", {"id": f"eq.{godown_id}", "company_id": f"eq.{company_id}"}, payload)
    if not rows:
        raise ApiError(404, "Godown not found")
    return rows[0]
