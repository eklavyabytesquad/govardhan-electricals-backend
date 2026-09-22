"""Companies: a user can belong to (and create) multiple companies (tables:
companies, company_members)."""
import re

import supabase
from errors import ApiError

ROLES = ("owner", "admin", "member")
# "creator:users!created_by(...)" is a PostgREST embed: it resolves created_by (a user id)
# into the actual creator's name/username in the same query, instead of a raw uuid.
COMPANY_COLS = (
    "id,name,gstin,owner_name,number,metadata,created_at,updated_at,"
    "created_by,updated_by,creator:users!created_by(name,username)"
)
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def _with_creator(row):
    """Flattens the embedded creator object into created_by_name / created_by_username."""
    creator = row.pop("creator", None)
    if isinstance(creator, list):
        creator = creator[0] if creator else None
    row["created_by_name"] = creator["name"] if creator else None
    row["created_by_username"] = creator["username"] if creator else None
    return row


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

    try:
        from services.units_service import seed_defaults  # local import: avoids a circular import at module load time
        seed_defaults(company["id"], user_id)
    except Exception as e:  # noqa: BLE001 — must never break company creation, e.g. if migrations/006 hasn't run yet
        print("Warning: could not seed default units (run migrations/006_customers_units_inventory_image.sql):", e)

    return {**company, "role": "owner"}


def update(user_id, company_id, name=None, gstin=None, owner_name=None, number=None, metadata=None):
    """Edit an existing company's details. Only an owner/admin may do this — see
    require_manager. There is deliberately no delete()."""
    require_manager(user_id, company_id)
    company_id = str(company_id or "")

    payload = _clean_details(gstin, owner_name, number, metadata)
    if name is not None:
        name = str(name).strip()
        if not name:
            raise ApiError(400, "Company name is required")
        payload["name"] = name
    payload["updated_by"] = user_id

    rows = supabase.update("companies", {"id": f"eq.{company_id}"}, payload)
    if not rows:
        raise ApiError(404, "Company not found")
    return rows[0]


def list_for_user(user_id):
    """All companies this user belongs to, with their role in each."""
    memberships = supabase.select("company_members", {"user_id": f"eq.{user_id}"}, "company_id,role,joined_at")
    if not memberships:
        return []
    ids = ",".join(m["company_id"] for m in memberships)
    rows = supabase.select("companies", {"id": f"in.({ids})"}, COMPANY_COLS)
    companies = {c["id"]: _with_creator(c) for c in rows}
    return [
        {**companies[m["company_id"]], "role": m["role"], "joined_at": m["joined_at"]}
        for m in memberships if m["company_id"] in companies
    ]


def role_in(user_id, company_id):
    rows = supabase.select(
        "company_members", {"company_id": f"eq.{company_id}", "user_id": f"eq.{user_id}"}, "role", 1
    )
    return rows[0]["role"] if rows else None


def require_member(user_id, company_id):
    """Used by inventory/series/invoice services: any member can view a company's data."""
    company_id = str(company_id or "")
    if not company_id:
        raise ApiError(400, "company_id is required")
    role = role_in(user_id, company_id)
    if not role:
        raise ApiError(403, "You are not a member of this company")
    return role


def require_manager(user_id, company_id):
    """Used by inventory/series/invoice services: only owner/admin can create or edit."""
    role = require_member(user_id, company_id)
    if role not in ("owner", "admin"):
        raise ApiError(403, "Only an owner or admin of this company can do this")
    return role


def add_member(requester_id, company_id, identifier, role="member"):
    from services.auth_service import find_user  # local import: avoids a circular import at module load time

    company_id = str(company_id or "")
    if not company_id:
        raise ApiError(400, "company_id is required")
    if role not in ROLES:
        raise ApiError(400, "Role must be owner, admin or member")
    if role_in(requester_id, company_id) not in ("owner", "admin"):
        raise ApiError(403, "Only an owner or admin of this company can add members")

    user = find_user(identifier, cols="id,name,username")
    if not user:
        raise ApiError(404, "No user found with that username or mobile number")
    if role_in(user["id"], company_id):
        raise ApiError(409, "That user is already a member of this company")

    supabase.insert("company_members", {"company_id": company_id, "user_id": user["id"], "role": role})
    return {"company_id": company_id, "user": user, "role": role}
