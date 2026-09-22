"""Companies: a user can belong to (and create) multiple companies (tables:
companies, company_members)."""
import supabase
from errors import ApiError

ROLES = ("owner", "admin", "member")


def create(user_id, name):
    name = str(name or "").strip()
    if not name:
        raise ApiError(400, "Company name is required")
    company = supabase.insert("companies", {"name": name, "created_by": user_id})
    supabase.insert("company_members", {"company_id": company["id"], "user_id": user_id, "role": "owner"})
    return {**company, "role": "owner"}


def list_for_user(user_id):
    """All companies this user belongs to, with their role in each."""
    memberships = supabase.select("company_members", {"user_id": f"eq.{user_id}"}, "company_id,role,joined_at")
    if not memberships:
        return []
    ids = ",".join(m["company_id"] for m in memberships)
    companies = {c["id"]: c for c in supabase.select("companies", {"id": f"in.({ids})"}, "id,name,created_at")}
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
