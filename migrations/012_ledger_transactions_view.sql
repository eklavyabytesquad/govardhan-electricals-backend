-- Migration 012: per-ledger transaction history, for a Tally-style printable
-- "ledger account" statement (every voucher that touched one ledger, dated,
-- in order, so the frontend can show a running balance).
--
-- Run in Supabase Dashboard -> SQL Editor, after 010_accounting_system.sql.

begin;

create view public.v_ledger_transactions as
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
  le.credit
from public.ledger_entries le
join public.vouchers v on v.company_id = le.company_id and v.id = le.voucher_id
join public.voucher_types vt on vt.company_id = v.company_id and vt.id = v.voucher_type_id
where v.status = 'active';

revoke all on public.v_ledger_transactions from anon, authenticated;

commit;
