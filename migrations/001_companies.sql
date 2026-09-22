-- Migration 001: companies + company_members
-- Adds multi-company support: a user can belong to several companies, and a
-- company can have several users, each with their own role in that company.
-- Safe to run on your existing database — every statement is
-- "if not exists", so it only adds what's missing and won't touch
-- users/sessions/tokens/otp_records/enquiries or any existing rows.
--
-- Run in Supabase Dashboard -> SQL Editor.

-- ============ companies ============
create table if not exists public.companies (
    id          uuid primary key default gen_random_uuid(),
    name        text        not null,
    created_by  uuid        not null references public.users(id) on delete restrict,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- ============ company_members (many-to-many: a user can belong to several companies,
-- and a company can have several users, each with their own role in that company) ============
create table if not exists public.company_members (
    id          uuid primary key default gen_random_uuid(),
    company_id  uuid        not null references public.companies(id) on delete cascade,
    user_id     uuid        not null references public.users(id) on delete cascade,
    role        text        not null default 'member' check (role in ('owner', 'admin', 'member')),
    joined_at   timestamptz not null default now(),
    unique (company_id, user_id)   -- a user can only join the same company once
);
create index if not exists company_members_user_idx on public.company_members(user_id);
create index if not exists company_members_company_idx on public.company_members(company_id);

-- ============ Row Level Security ============
-- No policies => only the backend's secret/service_role key can read or write
-- these tables, same as users/sessions/tokens/otp_records.
alter table public.companies       enable row level security;
alter table public.company_members enable row level security;
