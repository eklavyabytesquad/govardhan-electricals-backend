-- Migration 009: remove the entire invoicing/inventory feature set.
-- Keeps: users, sessions, tokens, otp_records, enquiries, companies, company_members
-- (everything needed to log in, and create/manage companies + members).
-- Drops, in dependency order (children before parents), WITH their data:
--   1. invoice_items   (references invoices, inventory)
--   2. invoice_config   (references companies, invoice_series)
--   3. invoices         (references companies, invoice_series)
--   4. inventory        (references companies)
--   5. customers        (references companies)
--   6. units            (references companies)
--   7. invoice_series   (references companies — safe last, nothing points to it anymore)
-- Plus the two functions that only existed to support those tables:
--   search_invoices(), next_invoice_number()
--
-- WARNING: this is destructive and irreversible — every invoice, inventory
-- item, customer, unit and series row is permanently deleted. Back up first
-- (Supabase Dashboard -> Database -> Backups, or pg_dump) if you want to keep
-- this data. Once you're sure, run this in Supabase Dashboard -> SQL Editor.

drop function if exists public.search_invoices(uuid, text, date, date, numeric, numeric);
drop function if exists public.next_invoice_number(uuid);

drop table if exists public.invoice_items  cascade;
drop table if exists public.invoice_config cascade;
drop table if exists public.invoices       cascade;
drop table if exists public.inventory      cascade;
drop table if exists public.customers      cascade;
drop table if exists public.units          cascade;
drop table if exists public.invoice_series cascade;
