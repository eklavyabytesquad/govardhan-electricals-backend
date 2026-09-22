"""Saved customers per company (table: customers) — lets invoices pull in a
customer's details instead of retyping them every time."""
import re

import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = "id,company_id,name,number,email,gstin,address,metadata,created_at,updated_at"


def _clean(name, number=None, email=None, gstin=None, address=None, metadata=None):
    name = str(name or "").strip()
    if not name:
        raise ApiError(400, "Customer name is required")
    payload = {"name": name}

    number = re.sub(r"[\s-]", "", str(number or ""))
    if number:
        payload["number"] = number

    email = str(email or "").strip()
    if email:
        payload["email"] = email

    gstin = str(gstin or "").strip().upper()
    if gstin:
        payload["gstin"] = gstin

    address = str(address or "").strip()
    if address:
        payload["address"] = address

    if metadata is not None:
        if not isinstance(metadata, dict):
            raise ApiError(400, "metadata must be a JSON object")
        payload["metadata"] = metadata

    return payload


def create(user_id, company_id, **fields):
    require_manager(user_id, company_id)
    payload = _clean(**fields)
    payload.update({"company_id": company_id, "created_by": user_id})
    return supabase.insert("customers", payload)


def list_for_company(user_id, company_id):
    require_member(user_id, company_id)
    return supabase.select("customers", {"company_id": f"eq.{company_id}"}, COLS, order="name.asc")


def update(user_id, company_id, customer_id, **fields):
    require_manager(user_id, company_id)
    customer_id = str(customer_id or "")
    if not customer_id:
        raise ApiError(400, "id is required")
    payload = _clean(**fields)
    payload["updated_by"] = user_id
    rows = supabase.update("customers", {"id": f"eq.{customer_id}", "company_id": f"eq.{company_id}"}, payload)
    if not rows:
        raise ApiError(404, "Customer not found")
    return rows[0]


def delete(user_id, company_id, customer_id):
    require_manager(user_id, company_id)
    customer_id = str(customer_id or "")
    if not customer_id:
        raise ApiError(400, "id is required")
    supabase.delete("customers", {"id": f"eq.{customer_id}", "company_id": f"eq.{company_id}"})
