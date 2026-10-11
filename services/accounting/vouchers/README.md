# Vouchers

The only way financial data gets written: purchases, sales, payments,
receipts, credit/debit notes, journals, contra, stock transfers, opening
stock, stock adjustments, orders, receipt/delivery notes — all one table
family (`vouchers` + `voucher_items` + `ledger_entries` +
`bill_allocations`), separated by `voucher_type_id`.

| Endpoint | Method | Auth | Does |
|---|---|---|---|
| `/api/accounting/vouchers` | `POST` | owner/admin | Create a voucher (see payload below) |
| `/api/accounting/vouchers?company_id=&voucher_type_id=&status=` | `GET` | any member | List vouchers, newest first |
| `/api/accounting/vouchers/get?company_id=&id=` | `GET` | any member | One voucher with its items / ledger entries / bill allocations |
| `/api/accounting/vouchers/cancel` | `POST` | owner/admin | `{company_id, id, reason?}` — sets `status = 'cancelled'` |

## Why this isn't plain REST inserts

A voucher's header, its stock lines, its debit/credit lines and its bill
allocations all have to land in **one transaction** — the DB has a deferred
trigger that checks every voucher's ledger entries sum to zero, and it only
has something to check once every leg has been inserted. Supabase PostgREST
makes every plain HTTP call its own transaction, so a sequence of separate
`POST`s could never satisfy that safely. Instead, `create()` and `cancel()`
both go through single Postgres functions —
`save_voucher(jsonb)` / `cancel_voucher(...)` — defined in
`migrations/011_save_voucher_rpc.sql`. One RPC call = one transaction.

## Create payload

```json
{
  "voucher_type_id": "...",
  "financial_year_id": "...",
  "voucher_no": "optional — omit to auto-number from the series",
  "date": "2026-10-11",
  "party_ledger_id": "optional — the customer/supplier ledger",
  "reference_no": "optional — the other party's own bill number",
  "reference_date": "optional",
  "place_of_supply": "optional — state code; same state as the company = CGST+SGST, else IGST",
  "narration": "optional",
  "items": [
    {
      "item_id": "...", "godown_id": "...", "batch_id": "optional",
      "qty_in": 0, "qty_out": 0, "rate": 0, "discount_amount": 0,
      "taxable_amount": 0, "gst_rate": 0, "cgst": 0, "sgst": 0, "igst": 0, "cess": 0,
      "line_total": 0, "cost_rate": "optional — filled on OUT lines for profit"
    }
  ],
  "ledger_entries": [
    { "ledger_id": "...", "debit": 0, "credit": 0 }
  ],
  "bill_allocations": [
    { "ledger_id": "...", "ref_type": "new | against | advance | on_account", "bill_voucher_id": "optional", "bill_ref": "optional", "due_date": "optional", "amount": 0 }
  ]
}
```

`company_id` and `created_by` are never read from this body — they always
come from the authenticated request (`require_manager` + the logged-in
user), so one company can never write into another's books.

At least one of `items` / `ledger_entries` is required; a voucher with
neither moves nothing and settles nothing, so it's rejected.

## The app builds the money lines — users never type debit/credit

Per the migration brief: CGST+SGST (half each) when `place_of_supply`
matches the company's own state, otherwise IGST. The **frontend** is
responsible for turning "Purchase, 10 units of Widget @ ₹50, 18% GST" into
the actual `items` + `ledger_entries` arrays above (Dr Purchase, Dr Input
GST, Cr Supplier) before calling `POST /api/accounting/vouchers` — this
backend only validates and persists, following the table in migration
010's brief for which voucher type writes which lines.

## Editing a voucher

There's no `/update` endpoint yet. Per migration 010's rule 6, an edit is
"delete the lines, re-insert them" inside the same transaction as the
header change — that needs its own RPC (`update_voucher`, mirroring
`save_voucher`), not built yet. For now, cancel the wrong voucher and create
a new one.

## Closed financial years

Every write above an underlying `financial_years.is_closed = true` year is
rejected by a DB trigger (migration 010), not by this service layer — so
even a bug here can't bypass it.
