"""Notifications (table: notifications, migration 015) — low-stock alerts
and payment reminders (bills overdue in either direction), fanned out one
row per company member so each person's read state is independent.

Rows are generated lazily in generate_for_company(), called from both
list_for_user() and unread_count() — there's no cron/trigger. The partial
unique index on (company_id, user_id, type, related_id) WHERE NOT is_read
is what makes calling this on every page load safe: inserting a duplicate
for a condition the user hasn't read yet just violates the index, which we
catch and ignore (the dedupe is also pre-filtered in Python to avoid firing
that error in the common case, but the index is the real guarantee)."""
import supabase
from errors import ApiError
from services.company_service import require_member
from supabase import SupabaseError

COLS = "id,company_id,user_id,type,related_id,severity,title,message,link,is_read,read_at,created_at"


def _company_member_ids(company_id):
    rows = supabase.select("company_members", {"company_id": f"eq.{company_id}"}, "user_id")
    return [r["user_id"] for r in rows]


def generate_for_company(company_id):
    """Best-effort: scans current conditions (low stock, overdue bills) and
    creates any notifications that don't already exist, unread, for each
    company member. Never raises — a failure here must never block a user
    from just seeing their existing notification list."""
    try:
        member_ids = _company_member_ids(company_id)
        if not member_ids:
            return

        existing = supabase.select(
            "notifications", {"company_id": f"eq.{company_id}", "is_read": "eq.false"}, "user_id,type,related_id",
        )
        seen = {(r["user_id"], r["type"], r["related_id"]) for r in existing}

        to_insert = []

        # -- low stock --
        low_stock = supabase.select("v_low_stock", {"company_id": f"eq.{company_id}"})
        for item in low_stock:
            for uid in member_ids:
                key = (uid, "low_stock", item["item_id"])
                if key in seen:
                    continue
                seen.add(key)
                to_insert.append({
                    "company_id": company_id, "user_id": uid, "type": "low_stock",
                    "related_id": item["item_id"], "severity": "warning",
                    "title": "Low stock",
                    "message": f"\"{item['name']}\" is down to {item['current_qty']} (reorder level {item['reorder_level']}).",
                    "link": "/dashboard/items-stock",
                })

        # -- payment reminders: bills overdue, either direction --
        groups = supabase.select("account_groups", {"company_id": f"eq.{company_id}"}, "id,name")
        debtor_group = next((g["id"] for g in groups if g["name"] == "Sundry Debtors"), None)
        ledgers = supabase.select("ledgers", {"company_id": f"eq.{company_id}"}, "id,name,group_id")
        ledger_by_id = {l["id"]: l for l in ledgers}

        outstanding = supabase.select("v_bill_outstanding", {"company_id": f"eq.{company_id}"})
        for bill in outstanding:
            if (bill.get("days_overdue") or 0) <= 0 or not bill.get("bill_voucher_id"):
                continue
            ledger = ledger_by_id.get(bill["ledger_id"])
            if not ledger:
                continue
            is_receivable = ledger["group_id"] == debtor_group
            for uid in member_ids:
                key = (uid, "bill_overdue", bill["bill_voucher_id"])
                if key in seen:
                    continue
                seen.add(key)
                amount = f"Rs. {float(bill['outstanding_amount']):.2f}"
                if is_receivable:
                    title = "Payment overdue"
                    message = f"{ledger['name']} owes {amount} — {bill['days_overdue']} days overdue."
                else:
                    title = "Payment due"
                    message = f"You owe {ledger['name']} {amount} — {bill['days_overdue']} days overdue."
                to_insert.append({
                    "company_id": company_id, "user_id": uid, "type": "bill_overdue",
                    "related_id": bill["bill_voucher_id"], "severity": "critical" if bill["days_overdue"] > 14 else "warning",
                    "title": title, "message": message, "link": "/dashboard/reports",
                })

        if to_insert:
            try:
                supabase.insert_many("notifications", to_insert)
            except SupabaseError:
                pass  # a rare race against another request's insert — fine, just skip this round
    except Exception:  # noqa: BLE001 — generation must never break the notification list itself
        pass


def list_for_user(user_id, company_id, limit=50):
    require_member(user_id, company_id)
    generate_for_company(company_id)
    return supabase.select(
        "notifications", {"company_id": f"eq.{company_id}", "user_id": f"eq.{user_id}"},
        COLS, limit, order="created_at.desc",
    )


def unread_count(user_id, company_id):
    require_member(user_id, company_id)
    generate_for_company(company_id)
    rows = supabase.select(
        "notifications", {"company_id": f"eq.{company_id}", "user_id": f"eq.{user_id}", "is_read": "eq.false"}, "id",
    )
    return len(rows)


def mark_read(user_id, company_id, notification_id):
    require_member(user_id, company_id)
    notification_id = str(notification_id or "")
    if not notification_id:
        raise ApiError(400, "id is required")
    rows = supabase.update(
        "notifications",
        {"id": f"eq.{notification_id}", "company_id": f"eq.{company_id}", "user_id": f"eq.{user_id}"},
        {"is_read": True, "read_at": "now"},
    )
    if not rows:
        raise ApiError(404, "Notification not found")
    return rows[0]


def mark_all_read(user_id, company_id):
    require_member(user_id, company_id)
    supabase.update(
        "notifications",
        {"company_id": f"eq.{company_id}", "user_id": f"eq.{user_id}", "is_read": "eq.false"},
        {"is_read": True, "read_at": "now"},
    )
    return {"ok": True}
