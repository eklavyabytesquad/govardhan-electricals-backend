"""Batches (table: batches) — one lot of an item, with mfg/expiry dates."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = "id,company_id,item_id,batch_no,mfg_date,expiry_date,created_at,updated_at"


def create(user_id, company_id, item_id, batch_no, mfg_date=None, expiry_date=None):
    require_manager(user_id, company_id)
    item_id = str(item_id or "")
    batch_no = str(batch_no or "").strip()
    if not item_id or not batch_no:
        raise ApiError(400, "item_id and batch_no are required")
    return supabase.insert("batches", {
        "company_id": company_id, "item_id": item_id, "batch_no": batch_no,
        "mfg_date": mfg_date or None, "expiry_date": expiry_date or None, "created_by": user_id,
    })


def list_for_company(user_id, company_id, item_id=None):
    require_member(user_id, company_id)
    filters = {"company_id": f"eq.{company_id}"}
    if item_id:
        filters["item_id"] = f"eq.{item_id}"
    return supabase.select("batches", filters, COLS, order="batch_no.asc")


def update(user_id, company_id, batch_id, mfg_date=None, expiry_date=None):
    require_manager(user_id, company_id)
    batch_id = str(batch_id or "")
    if not batch_id:
        raise ApiError(400, "id is required")
    payload = {"updated_by": user_id}
    if mfg_date is not None:
        payload["mfg_date"] = mfg_date or None
    if expiry_date is not None:
        payload["expiry_date"] = expiry_date or None
    rows = supabase.update("batches", {"id": f"eq.{batch_id}", "company_id": f"eq.{company_id}"}, payload)
    if not rows:
        raise ApiError(404, "Batch not found")
    return rows[0]
