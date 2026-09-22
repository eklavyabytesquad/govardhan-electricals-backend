"""Inventory items per company (table: inventory)."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = (
    "id,company_id,sku,name,description,hsn_code,unit,price,tax_rate,"
    "stock_quantity,reorder_level,category,metadata,created_at,updated_at"
)


def _num(value, default=0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ApiError(400, "Invalid number")


def _clean(name, sku=None, description=None, hsn_code=None, unit=None, price=None,
           tax_rate=None, stock_quantity=None, reorder_level=None, category=None, metadata=None):
    name = str(name or "").strip()
    if not name:
        raise ApiError(400, "Item name is required")
    payload = {
        "name": name,
        "sku": str(sku).strip() or None if sku is not None else None,
        "description": str(description or "").strip() or None,
        "hsn_code": str(hsn_code or "").strip() or None,
        "unit": str(unit or "Nos").strip() or "Nos",
        "price": _num(price),
        "tax_rate": _num(tax_rate),
        "stock_quantity": _num(stock_quantity),
        "reorder_level": _num(reorder_level),
        "category": str(category or "").strip() or None,
    }
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise ApiError(400, "metadata must be a JSON object")
        payload["metadata"] = metadata
    return payload


def create(user_id, company_id, **fields):
    require_manager(user_id, company_id)
    payload = _clean(**fields)
    payload.update({"company_id": company_id, "created_by": user_id})
    return supabase.insert("inventory", payload)


def list_for_company(user_id, company_id):
    require_member(user_id, company_id)
    return supabase.select("inventory", {"company_id": f"eq.{company_id}"}, COLS, order="name.asc")


def update(user_id, company_id, item_id, **fields):
    require_manager(user_id, company_id)
    item_id = str(item_id or "")
    if not item_id:
        raise ApiError(400, "id is required")
    payload = _clean(**fields)
    payload["updated_by"] = user_id
    rows = supabase.update("inventory", {"id": f"eq.{item_id}", "company_id": f"eq.{company_id}"}, payload)
    if not rows:
        raise ApiError(404, "Item not found")
    return rows[0]


def delete(user_id, company_id, item_id):
    require_manager(user_id, company_id)
    item_id = str(item_id or "")
    if not item_id:
        raise ApiError(400, "id is required")
    supabase.delete("inventory", {"id": f"eq.{item_id}", "company_id": f"eq.{company_id}"})
