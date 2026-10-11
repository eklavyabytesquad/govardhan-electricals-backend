"""Account groups (table: account_groups) — the tree ledgers hang off of,
and that P&L / Balance Sheet reports are built from. System groups (seeded
automatically per company, migration 010) can't be renamed or re-parented."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = "id,company_id,name,parent_id,nature,affects_gross_profit,is_system,created_at,updated_at"
NATURES = ("asset", "liability", "income", "expense")


def create(user_id, company_id, name, parent_id=None, nature=None, affects_gross_profit=False):
    require_manager(user_id, company_id)
    name = str(name or "").strip()
    if not name:
        raise ApiError(400, "Group name is required")
    if nature not in NATURES:
        raise ApiError(400, "nature must be one of: " + ", ".join(NATURES))
    return supabase.insert("account_groups", {
        "company_id": company_id, "name": name, "parent_id": parent_id or None,
        "nature": nature, "affects_gross_profit": bool(affects_gross_profit), "created_by": user_id,
    })


def list_for_company(user_id, company_id):
    require_member(user_id, company_id)
    return supabase.select("account_groups", {"company_id": f"eq.{company_id}"}, COLS, order="name.asc")


def update(user_id, company_id, group_id, name=None, parent_id=None):
    require_manager(user_id, company_id)
    group_id = str(group_id or "")
    if not group_id:
        raise ApiError(400, "id is required")

    existing = supabase.select(
        "account_groups", {"id": f"eq.{group_id}", "company_id": f"eq.{company_id}"}, "id,is_system", 1
    )
    if not existing:
        raise ApiError(404, "Account group not found")
    if existing[0]["is_system"]:
        raise ApiError(403, "System groups can't be renamed or re-parented")

    payload = {"updated_by": user_id}
    if name is not None:
        name = str(name).strip()
        if not name:
            raise ApiError(400, "Group name is required")
        payload["name"] = name
    if parent_id is not None:
        payload["parent_id"] = parent_id or None

    return supabase.update("account_groups", {"id": f"eq.{group_id}"}, payload)[0]
