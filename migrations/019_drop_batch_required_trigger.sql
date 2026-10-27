-- Migration 019: drop the batch-required trigger (migration 010,
-- check_voucher_item_batch / trg_voucher_items_batch_required).
--
-- Batch tracking was removed from the frontend entirely — the New Sale/
-- Purchase form no longer has a batch picker (migration 018 made godown_id
-- optional for the same reason), and the Items form no longer has a
-- "Tracks batches" checkbox. But any item created before that change still
-- has track_batches=true sitting in its row, and this trigger kept
-- enforcing "batch_id is required on every voucher line" for those items
-- even though there's no UI left to ever supply one — blocking every sale
-- or purchase of that item. Since the feature is gone, the rule enforcing
-- it needs to go too.
--
-- Run in Supabase Dashboard -> SQL Editor, after 010_accounting_system.sql.

begin;

drop trigger if exists trg_voucher_items_batch_required on public.voucher_items;
drop function if exists public.check_voucher_item_batch();

commit;
