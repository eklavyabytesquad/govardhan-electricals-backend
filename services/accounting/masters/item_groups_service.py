"""Item (product) categories, a tree (table: item_groups)."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = "id,company_id,name,parent_id,created_at,updated_at"


def create(user_id, company_id, name, parent_id=None):
    require_manager(user_id, company_id)
    name = str(name or "").strip()
    if not name:
        raise ApiError(400, "Group name is required")
    return supabase.insert("item_groups", {
        "company_id": company_id, "name": name, "parent_id": parent_id or None, "created_by": user_id,
    })


def list_for_company(user_id, company_id):
    require_member(user_id, company_id)
    return supabase.select("item_groups", {"company_id": f"eq.{company_id}"}, COLS, order="name.asc")


def update(user_id, company_id, group_id, name=None, parent_id=None):
    require_manager(user_id, company_id)
    group_id = str(group_id or "")
    if not group_id:
        raise ApiError(400, "id is required")
    payload = {"updated_by": user_id}
    if name is not None:
        name = str(name).strip()
        if not name:
            raise ApiError(400, "Group name is required")
        payload["name"] = name
    if parent_id is not None:
        payload["parent_id"] = parent_id or None
    rows = supabase.update("item_groups", {"id": f"eq.{group_id}", "company_id": f"eq.{company_id}"}, payload)
    if not rows:
        raise ApiError(404, "Item group not found")
    return rows[0]
