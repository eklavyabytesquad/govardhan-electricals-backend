"""Company-defined unit types (table: units) — e.g. Kg, Dozen, Pcs, Sets,
Kits, Bundle. Used to populate the unit dropdown on inventory/invoice items."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

COLS = "id,company_id,name,abbreviation,created_at"

DEFAULT_UNITS = [
    ("Numbers", "Nos"), ("Pieces", "Pcs"), ("Kilogram", "Kg"), ("Dozen", "Dz"),
    ("Sets", "Sets"), ("Kits", "Kits"), ("Bundle", "Bundle"), ("Meter", "Mtr"), ("Litre", "Ltr"),
]


def seed_defaults(company_id, user_id):
    """Called once when a company is created, so the unit dropdown isn't empty."""
    rows = [
        {"company_id": company_id, "name": name, "abbreviation": abbr, "created_by": user_id}
        for name, abbr in DEFAULT_UNITS
    ]
    supabase.insert_many("units", rows)


def create(user_id, company_id, name, abbreviation=None):
    require_manager(user_id, company_id)
    name = str(name or "").strip()
    if not name:
        raise ApiError(400, "Unit name is required")
    payload = {
        "company_id": company_id, "name": name,
        "abbreviation": str(abbreviation or "").strip() or None, "created_by": user_id,
    }
    return supabase.insert("units", payload)


def list_for_company(user_id, company_id):
    require_member(user_id, company_id)
    return supabase.select("units", {"company_id": f"eq.{company_id}"}, COLS, order="name.asc")


def delete(user_id, company_id, unit_id):
    require_manager(user_id, company_id)
    unit_id = str(unit_id or "")
    if not unit_id:
        raise ApiError(400, "id is required")
    supabase.delete("units", {"id": f"eq.{unit_id}", "company_id": f"eq.{company_id}"})
