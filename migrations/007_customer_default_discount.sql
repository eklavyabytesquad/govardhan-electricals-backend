-- Migration 007: per-customer default discount.
-- When a customer is picked on an invoice, this discount % auto-fills every
-- line item (still editable per line).
-- Safe to run on your existing database.
--
-- Run in Supabase Dashboard -> SQL Editor.

alter table public.customers
    add column if not exists default_discount_percent numeric(5,2) not null default 0;
