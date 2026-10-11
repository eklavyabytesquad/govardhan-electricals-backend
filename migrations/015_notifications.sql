-- Migration 015: notifications — low-stock alerts, payment reminders (bills
-- overdue in either direction), and room for more types later.
--
-- One row per (company, recipient user): a company-wide condition like "Item
-- X is low on stock" is fanned out into one row per company member, so each
-- person's read/unread state is independent instead of a single shared flag.
--
-- Rows are generated lazily by the backend (notification_service.py) when a
-- user asks for their notification list — there's no cron/trigger here.
-- `related_id` is what makes that safe to call repeatedly: before creating
-- a new "low stock" row for item X, the service checks whether this user
-- already has an UNREAD one with the same (type, related_id), so refreshing
-- the page doesn't spam duplicates. Once marked read, the same condition
-- (if still true) will notify again — matching how most notification
-- systems behave in practice.
--
-- Run in Supabase Dashboard -> SQL Editor, after 010_accounting_system.sql.

begin;

create table public.notifications (
  id            uuid primary key default gen_random_uuid(),
  company_id    uuid not null references public.companies(id) on delete cascade,
  user_id       uuid not null references public.users(id) on delete cascade,
  type          text not null check (type in ('low_stock', 'bill_overdue', 'custom')),
  related_id    uuid,
  severity      text not null default 'info' check (severity in ('info', 'warning', 'critical')),
  title         text not null,
  message       text not null,
  link          text,
  is_read       boolean not null default false,
  read_at       timestamptz,
  created_at    timestamptz not null default now()
);

create index notifications_user_unread_idx on public.notifications(company_id, user_id, is_read, created_at desc);
-- Partial unique index used for the generation-time dedupe check (only one
-- UNREAD notification per user/type/related_id at a time).
create unique index notifications_dedupe_key on public.notifications(company_id, user_id, type, related_id) where not is_read;

alter table public.notifications enable row level security;

commit;
