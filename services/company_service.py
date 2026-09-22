"""Companies: a user can belong to (and create) multiple companies (tables:
companies, company_members)."""
import re

import supabase
from errors import ApiError

ROLES = ("owner", "admin", "member")
COMPANY_COLS = "id,name,gstin,owner_name,number,metadata,created_at"
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def _clean_details(gstin, owner_name, number, metadata):
    """Validate the optional company-detail fields; returns a dict to merge into the insert."""
    details = {}

    gstin = str(gstin or "").strip().upper()
    if gstin:
        if not GSTIN_RE.match(gstin):
            raise ApiError(400, "Invalid GSTIN format")
        details["gstin"] = gstin

    owner_name = str(owner_name or "").strip()
    if owner_name:
        details["owner_name"] = owner_name

    number = re.sub(r"[\s-]", "", str(number or ""))
    if number:
        if not (number.isdigit() and 6 <= len(number) <= 15):
            raise ApiError(400, "Enter a valid company contact number")
        details["number"] = number

    if metadata is not None:
        if not isinstance(metadata, dict):
            raise ApiError(400, "metadata must be a JSON object")
        details["metadata"] = metadata

    return details


def create(user_id, name, gstin=None, owner_name=None, number=None, metadata=None):
    name = str(name or "").strip()
    if not name:
        raise ApiError(400, "Company name is required")
    payload = {"name": name, "created_by": user_id, **_clean_details(gstin, owner_name, number, metadata)}
    company = supabase.insert("companies", payload)
    supabase.insert("company_members", {"company_id": company["id"], "user_id": user_id, "role": "owner"})
    return {**company, "role": "owner"}


def list_for_user(user_id):
    """All companies this user belongs to, with their role in each."""
    memberships = supabase.select("company_members", {"user_id": f"eq.{user_id}"}, "company_id,role,joined_at")
    if not memberships:
        return []
    ids = ",".join(m["company_id"] for m in memberships)
    companies = {c["id"]: c for c in supabase.select("companies", {"id": f"in.({ids})"}, COMPANY_COLS)}
    return [
        {**companies[m["company_id"]], "role": m["role"], "joined_at": m["joined_at"]}
        for m in memberships if m["company_id"] in companies
    ]


def _role_in(user_id, company_id):
    rows = supabase.select(
        "company_members", {"company_id": f"eq.{company_id}", "user_id": f"eq.{user_id}"}, "role", 1
    )
    return rows[0]["role"] if rows else None


def add_member(requester_id, company_id, identifier, role="member"):
    from services.auth_service import find_user  # local import: avoids a circular import at module load time

    company_id = str(company_id or "")
    if not company_id:
        raise ApiError(400, "company_id is required")
    if role not in ROLES:
        raise ApiError(400, "Role must be owner, admin or member")
    if _role_in(requester_id, company_id) not in ("owner", "admin"):
        raise ApiError(403, "Only an owner or admin of this company can add members")

    user = find_user(identifier, cols="id,name,username")
    if not user:
        raise ApiError(404, "No user found with that username or mobile number")
    if _role_in(user["id"], company_id):
        raise ApiError(409, "That user is already a member of this company")

    supabase.insert("company_members", {"company_id": company_id, "user_id": user["id"], "role": role})
    return {"company_id": company_id, "user": user, "role": role}
