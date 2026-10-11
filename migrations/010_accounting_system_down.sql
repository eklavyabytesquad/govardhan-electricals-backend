-- Down migration for 010_accounting_system.sql.
-- Removes everything that migration added, in reverse order, and does NOT
-- touch users/sessions/tokens/otp_records/enquiries/companies/company_members.
--
-- WARNING: destructive and irreversible — every financial year, ledger,
-- item, voucher, voucher line and balance is permanently deleted. Back up
-- first if you want to keep this data.
--
-- Run in Supabase Dashboard -> SQL Editor.

begin;

-- ---- views ----
drop view if exists public.v_sales_profit cascade;
drop view if exists public.v_bill_outstanding cascade;
drop view if exists public.v_ledger_balance cascade;
drop view if exists public.v_low_stock cascade;
drop view if exists public.v_batch_expiry cascade;
drop view if exists public.v_stock_value cascade;
drop view if exists public.v_stock_balance cascade;

-- ---- seed-on-company-create wiring ----
drop trigger if exists trg_companies_seed_defaults on public.companies;
drop function if exists public.trg_seed_company_defaults();
drop function if exists public.seed_company_defaults(uuid, uuid);

-- ---- entry tables (reverse dependency order) ----
drop table if exists public.voucher_links      cascade;
drop table if exists public.bill_allocations   cascade;
drop table if exists public.ledger_entries     cascade;
drop table if exists public.voucher_items      cascade;
drop table if exists public.vouchers           cascade;

-- ---- setup tables (reverse dependency order) ----
drop table if exists public.voucher_number_series cascade;
drop table if exists public.voucher_types         cascade;
drop table if exists public.batches               cascade;
drop table if exists public.godowns               cascade;
drop table if exists public.items                 cascade;
drop table if exists public.item_groups           cascade;
drop table if exists public.units                 cascade;
drop table if exists public.ledgers               cascade;
drop table if exists public.account_groups        cascade;
drop table if exists public.financial_years       cascade;

-- ---- standalone trigger/check functions ----
-- (set_updated_at is dropped last in case a future migration still wants it;
--  drop it too if you're sure nothing else uses it.)
drop function if exists public.check_voucher_item_batch();
drop function if exists public.check_fy_open_for_voucher();
drop function if exists public.check_fy_open_for_vouchers_table();
drop function if exists public.check_voucher_balanced();
drop function if exists public.set_updated_at();

commit;
