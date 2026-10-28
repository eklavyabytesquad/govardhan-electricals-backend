"""Vouchers — purchase/sales/payment/receipt/returns/journal/contra/stock
movements, stored across vouchers + voucher_items + ledger_entries +
bill_allocations (migration 010). Every write goes through the
save_voucher() / cancel_voucher() Postgres functions (migration 011) so the
whole voucher lands in one transaction — see that migration's comments for
why plain REST inserts can't do this safely over PostgREST."""
import supabase
from errors import ApiError
from services.company_service import require_manager, require_member

VOUCHER_COLS = (
    "id,company_id,voucher_type_id,financial_year_id,voucher_no,date,party_ledger_id,"
    "reference_no,reference_date,place_of_supply,narration,total_amount,status,"
    "cancelled_at,cancelled_by,cancel_reason,created_by,updated_by,created_at,updated_at"
)
ITEM_COLS = (
    "id,voucher_id,line_no,item_id,godown_id,batch_id,qty_in,qty_out,rate,discount_amount,"
    "taxable_amount,gst_rate,cgst,sgst,igst,cess,line_total,cost_rate"
)
ENTRY_COLS = "id,voucher_id,ledger_id,debit,credit"
ALLOC_COLS = "id,voucher_id,ledger_id,ref_type,bill_voucher_id,bill_ref,due_date,amount"


def create(user_id, company_id, data):
    """data: {voucher_type_id, financial_year_id, date?, voucher_no?,
    party_ledger_id?, reference_no?, reference_date?, place_of_supply?,
    narration?, items?:[...], ledger_entries?:[...], bill_allocations?:[...]}.

    voucher_no is optional — omit it to auto-number from the company's
    series for this voucher type + financial year. company_id/created_by
    are always taken from the authenticated request, never from the body."""
    require_manager(user_id, company_id)
    if not data.get("voucher_type_id") or not data.get("financial_year_id"):
        raise ApiError(400, "voucher_type_id and financial_year_id are required")
    if not data.get("items") and not data.get("ledger_entries"):
        raise ApiError(400, "A voucher needs at least one item or ledger entry")

    payload = {**data, "company_id": company_id, "created_by": user_id}
    result = supabase.rpc("save_voucher", {"p_payload": payload})
    return get_one(user_id, company_id, result["id"])


def update(user_id, company_id, voucher_id, data):
    """Edits an existing ACTIVE voucher in place (same id/voucher_no) — the
    update_voucher() RPC (migration 017) deletes and reinserts its items/
    ledger_entries/bill_allocations inside one transaction, same balancing
    rules as create(). A cancelled voucher can't be edited — re-enter it."""
    require_manager(user_id, company_id)
    voucher_id = str(voucher_id or "")
    if not voucher_id:
        raise ApiError(400, "id is required")
    if not data.get("items") and not data.get("ledger_entries"):
        raise ApiError(400, "A voucher needs at least one item or ledger entry")

    payload = {**data, "id": voucher_id, "company_id": company_id, "updated_by": user_id}
    result = supabase.rpc("update_voucher", {"p_payload": payload})
    return get_one(user_id, company_id, result["id"])


def list_for_company(user_id, company_id, voucher_type_id=None, status=None):
    require_member(user_id, company_id)
    filters = {"company_id": f"eq.{company_id}"}
    if voucher_type_id:
        filters["voucher_type_id"] = f"eq.{voucher_type_id}"
    if status:
        filters["status"] = f"eq.{status}"
    return supabase.select("vouchers", filters, VOUCHER_COLS, order="date.desc,created_at.desc")


def get_one(user_id, company_id, voucher_id):
    require_member(user_id, company_id)
    voucher_id = str(voucher_id or "")
    rows = supabase.select("vouchers", {"id": f"eq.{voucher_id}", "company_id": f"eq.{company_id}"}, VOUCHER_COLS, 1)
    if not rows:
        raise ApiError(404, "Voucher not found")
    voucher = rows[0]
    voucher["items"] = supabase.select("voucher_items", {"voucher_id": f"eq.{voucher_id}"}, ITEM_COLS, order="line_no.asc")
    voucher["ledger_entries"] = supabase.select("ledger_entries", {"voucher_id": f"eq.{voucher_id}"}, ENTRY_COLS)
    voucher["bill_allocations"] = supabase.select("bill_allocations", {"voucher_id": f"eq.{voucher_id}"}, ALLOC_COLS)
    return voucher


def cancel(user_id, company_id, voucher_id, reason=None):
    require_manager(user_id, company_id)
    voucher_id = str(voucher_id or "")
    if not voucher_id:
        raise ApiError(400, "id is required")
    supabase.rpc("cancel_voucher", {
        "p_company_id": company_id, "p_voucher_id": voucher_id, "p_user_id": user_id, "p_reason": reason,
    })
    return get_one(user_id, company_id, voucher_id)


def delete_permanent(user_id, company_id, voucher_id):
    """Permanently removes a CANCELLED voucher — a real SQL DELETE, not
    just the status flip cancel() does. Safe to do with one plain REST
    delete (no custom RPC needed): every child row that only ever belonged
    to this voucher (voucher_items, ledger_entries, its own bill_allocations
    row, voucher_links) is ON DELETE CASCADE (migration 010), so removing
    the vouchers row cleans all of it up in the same statement.

    The one thing this can't bypass, on purpose: bill_allocations.bill_voucher_id
    is ON DELETE RESTRICT, so if some OTHER voucher (a payment/receipt, or a
    credit/debit note) is settled "against" this one, Postgres itself
    refuses the delete — cancelling already freed this voucher's own
    stock/ledger effect (every balance view filters status='active'), so
    only a genuinely unreferenced voucher can ever be deleted outright."""
    require_manager(user_id, company_id)
    voucher_id = str(voucher_id or "")
    if not voucher_id:
        raise ApiError(400, "id is required")
    rows = supabase.select("vouchers", {"id": f"eq.{voucher_id}", "company_id": f"eq.{company_id}"}, "id,status", 1)
    if not rows:
        raise ApiError(404, "Voucher not found")
    if rows[0]["status"] != "cancelled":
        raise ApiError(400, "Only a cancelled voucher can be permanently deleted — cancel it first.")

    # Look up anything else still settled against this bill BEFORE
    # attempting the delete, so the error can name it — e.g. "RCT/0001,
    # RCT/0002 are settled against this bill" — instead of the generic
    # "another voucher" a raw FK-violation message would otherwise give.
    # This is what blocks deleting a sales invoice from the Sales page
    # over a receipt that lives on the Payments page with no visible link
    # between the two in the UI otherwise.
    allocations = supabase.select("bill_allocations", {"bill_voucher_id": f"eq.{voucher_id}"}, "voucher_id")
    blocker_ids = sorted({a["voucher_id"] for a in allocations if a["voucher_id"] != voucher_id})
    if blocker_ids:
        blockers = supabase.select("vouchers", {"id": f"in.({','.join(blocker_ids)})"}, "voucher_no")
        names = ", ".join(b["voucher_no"] for b in blockers) or "another voucher"
        verb = "is" if len(blockers) == 1 else "are"
        raise ApiError(409, f"Can't delete — {names} {verb} settled against this bill. Delete {'it' if len(blockers) == 1 else 'those'} first, then this one.")

    try:
        # Every sale/purchase creates its OWN "new" bill_allocations row,
        # which self-references this same voucher on both FK columns
        # (voucher_id = this voucher, AND bill_voucher_id = this voucher).
        # Deleting the vouchers row would cascade that row away via
        # voucher_id — but the bill_voucher_id RESTRICT check fires first
        # and sees that same row still pointing at this voucher, so it
        # blocks the delete even when nothing else actually depends on it.
        # Removing this voucher's own bill_allocations rows explicitly,
        # first, avoids tripping that on itself.
        supabase.delete("bill_allocations", {"voucher_id": f"eq.{voucher_id}", "company_id": f"eq.{company_id}"})
        supabase.delete("vouchers", {"id": f"eq.{voucher_id}", "company_id": f"eq.{company_id}"})
    except supabase.SupabaseError as e:
        if e.code == "23503":
            raise ApiError(409, "Can't delete — another voucher (a payment, credit/debit note, etc.) is settled against this bill. Undo or delete that one first.")
        raise
    return {"id": voucher_id, "deleted": True}


def get_public_sale(voucher_id):
    """NO auth — backs the public /invoice/<id> page and the QR code printed
    on sales bills. A voucher's id is effectively a random, unguessable
    capability (same trust model as any "anyone with the link" share URL),
    but to keep the blast radius of that tiny: this only ever serves SALES
    vouchers (never purchase/payment/journal/internal ones), and the
    response is fully denormalized to just what's already printed on the
    paper copy handed to the customer — no internal ids, no ledger_entries,
    no other vouchers, no other company data."""
    voucher_id = str(voucher_id or "")
    if not voucher_id:
        raise ApiError(400, "id is required")

    rows = supabase.select(
        "vouchers", {"id": f"eq.{voucher_id}"},
        "id,company_id,voucher_type_id,voucher_no,date,status,party_ledger_id,total_amount,narration", 1,
    )
    if not rows:
        raise ApiError(404, "Invoice not found")
    voucher = rows[0]

    vt_rows = supabase.select("voucher_types", {"id": f"eq.{voucher['voucher_type_id']}"}, "code", 1)
    if not vt_rows or vt_rows[0]["code"] != "sales":
        raise ApiError(404, "Invoice not found")  # purchases/payments/etc. are never publicly viewable

    company_rows = supabase.select(
        "companies", {"id": f"eq.{voucher['company_id']}"}, "name,gstin,owner_name,number,metadata", 1,
    )
    company = company_rows[0] if company_rows else {}
    meta = company.get("metadata") or {}

    party = None
    if voucher.get("party_ledger_id"):
        party_rows = supabase.select(
            "ledgers", {"id": f"eq.{voucher['party_ledger_id']}"}, "name,gstin,address,phone", 1,
        )
        party = party_rows[0] if party_rows else None

    item_rows = supabase.select(
        "voucher_items", {"voucher_id": f"eq.{voucher_id}"},
        "item_id,qty_in,qty_out,rate,taxable_amount,gst_rate,cgst,sgst,igst,line_total",
        order="line_no.asc",
    )
    item_ids = list({r["item_id"] for r in item_rows if r.get("item_id")})
    items_by_id = {}
    if item_ids:
        items_by_id = {
            i["id"]: i for i in supabase.select("items", {"id": f"in.({','.join(item_ids)})"}, "id,name,hsn_code")
        }

    items = [{
        "name": items_by_id.get(r["item_id"], {}).get("name", "Item"),
        "hsn_code": items_by_id.get(r["item_id"], {}).get("hsn_code"),
        "qty": r["qty_in"] or r["qty_out"] or 0,
        "rate": r["rate"],
        "taxable_amount": r["taxable_amount"],
        "gst_rate": r["gst_rate"],
        "cgst": r["cgst"], "sgst": r["sgst"], "igst": r["igst"],
        "line_total": r["line_total"],
    } for r in item_rows]

    return {
        "company": {
            "name": company.get("name"), "gstin": company.get("gstin"),
            "owner_name": company.get("owner_name"), "number": company.get("number"),
            "logo_url": meta.get("logo_url"),
            "address": meta.get("address"), "city": meta.get("city"),
            "state": meta.get("state"), "pincode": meta.get("pincode"),
            "bank_name": meta.get("bank_name"), "bank_account_name": meta.get("bank_account_name"),
            "bank_account_number": meta.get("bank_account_number"), "bank_ifsc": meta.get("bank_ifsc"),
            "bank_branch": meta.get("bank_branch"), "upi_id": meta.get("upi_id"),
        },
        "voucher": {
            "voucher_no": voucher["voucher_no"], "date": voucher["date"], "status": voucher["status"],
            "total_amount": voucher["total_amount"], "narration": voucher.get("narration"),
        },
        "party": party,
        "items": items,
    }
