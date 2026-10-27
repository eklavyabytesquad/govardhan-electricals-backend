-- Migration 018: godown_id on voucher_items is no longer required.
--
-- The New Sale/Purchase form no longer asks the user to pick a godown
-- (location) or batch per line — most small businesses don't track
-- multi-location stock and found the extra fields just noise. batch_id was
-- already nullable; this brings godown_id in line with it. The existing
-- FK (voucher_items_godown_fkey, migration 010) still works fine on a
-- nullable column — Postgres simply skips the FK check when the value is
-- NULL — so lines without a godown just group under "no location" in
-- stock reports instead of being rejected.
--
-- Run in Supabase Dashboard -> SQL Editor, after 010_accounting_system.sql.

begin;

alter table public.voucher_items
  alter column godown_id drop not null;

commit;
