"""Products / stock items (table: items)."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = (
    "id,company_id,name,sku,group_id,unit_id,hsn_code,gst_rate,track_batches,track_expiry,"
    "reorder_level,default_sale_rate,default_purchase_rate,is_active,created_at,updated_at"
)
FIELDS = (
    "name", "sku", "group_id", "unit_id", "hsn_code", "gst_rate", "track_batches", "track_expiry",
    "reorder_level", "default_sale_rate", "default_purchase_rate", "is_active",
)


def _payload(data):
    return {f: data[f] for f in FIELDS if f in data}


def create(user_id, company_id, data):
    require_manager(user_id, company_id)
    name = str(data.get("name") or "").strip()
    if not name:
        raise ApiError(400, "Item name is required")
    if not data.get("unit_id"):
        raise ApiError(400, "unit_id is required")
    payload = {"company_id": company_id, "created_by": user_id, **_payload(data)}
    payload["name"] = name
    return supabase.insert("items", payload)


def list_for_company(user_id, company_id):
    require_member(user_id, company_id)
    return supabase.select("items", {"company_id": f"eq.{company_id}"}, COLS, order="name.asc")


def update(user_id, company_id, item_id, data):
    require_manager(user_id, company_id)
    item_id = str(item_id or "")
    if not item_id:
        raise ApiError(400, "id is required")
    payload = {"updated_by": user_id, **_payload(data)}
    if "name" in payload:
        payload["name"] = str(payload["name"]).strip()
        if not payload["name"]:
            raise ApiError(400, "Item name is required")
    rows = supabase.update("items", {"id": f"eq.{item_id}", "company_id": f"eq.{company_id}"}, payload)
    if not rows:
        raise ApiError(404, "Item not found")
    return rows[0]
