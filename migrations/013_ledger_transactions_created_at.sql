-- Migration 013: fixes v_ledger_transactions ordering within the same day.
--
-- Bug: the ledger statement sorted by (date, voucher_no), and voucher_no is
-- text — "PMT/0001" sorts before "PUR/0001" alphabetically (M < U) even
-- when the purchase was entered first and the payment second. Same-day
-- entries need to sort by when they actually happened, not by the voucher
-- prefix's letters.
--
-- Fix: expose the voucher's created_at so (date, created_at) is the real
-- chronological order, and the frontend can show a time alongside the date.
--
-- Run in Supabase Dashboard -> SQL Editor, after 012_ledger_transactions_view.sql.
-- CREATE OR REPLACE VIEW can only append columns, never reorder/remove them,
-- so this is safe to run even if something already reads the old shape.

begin;

create or replace view public.v_ledger_transactions as
select
  le.company_id,
  le.ledger_id,
  le.voucher_id,
  v.voucher_no,
  v.date,
  vt.name as voucher_type_name,
  vt.code as voucher_type_code,
  v.narration,
  le.debit,
  le.credit,
  v.created_at
from public.ledger_entries le
join public.vouchers v on v.company_id = le.company_id and v.id = le.voucher_id
join public.voucher_types vt on vt.company_id = v.company_id and vt.id = v.voucher_type_id
where v.status = 'active';

revoke all on public.v_ledger_transactions from anon, authenticated;

commit;
