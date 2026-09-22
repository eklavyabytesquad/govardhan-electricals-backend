-- Migration 008: CGST/SGST/IGST per line item, not just a single invoice-wide split.
-- tax_rate/tax_amount are kept as the SUM of the three (cgst+sgst+igst), so
-- invoices.tax_total (sum of item tax_amount) still means the same thing —
-- nothing else needs to change.
-- Safe to run on your existing database.
--
-- Run in Supabase Dashboard -> SQL Editor.

alter table public.invoice_items add column if not exists cgst_rate   numeric(5,2)  not null default 0;
alter table public.invoice_items add column if not exists sgst_rate   numeric(5,2)  not null default 0;
alter table public.invoice_items add column if not exists igst_rate   numeric(5,2)  not null default 0;
alter table public.invoice_items add column if not exists cgst_amount numeric(12,2) not null default 0;
alter table public.invoice_items add column if not exists sgst_amount numeric(12,2) not null default 0;
alter table public.invoice_items add column if not exists igst_amount numeric(12,2) not null default 0;
