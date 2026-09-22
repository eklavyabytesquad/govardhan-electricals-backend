-- Migration 006: customers, units (custom unit types), inventory images.
-- Safe to run on your existing database.
--
-- Run in Supabase Dashboard -> SQL Editor.

-- ============ customers ============
-- A saved customer list per company, so invoices don't need re-typing
-- customer details every time.
create table if not exists public.customers (
    id          uuid primary key default gen_random_uuid(),
    company_id  uuid        not null references public.companies(id) on delete cascade,
    name        text        not null,
    number      text,                                  -- mobile number
    email       text,
    gstin       text,
    address     text,
    metadata    jsonb       not null default '{}'::jsonb,
    created_by  uuid        not null references public.users(id) on delete restrict,
    updated_by  uuid        references public.users(id) on delete set null,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index if not exists customers_company_idx on public.customers(company_id);

-- ============ units ============
-- Company-defined unit types (Kg, Dozen, Pcs, Sets, Kits, Bundle, ...), so
-- inventory/invoice "unit" fields can be a dropdown instead of free text.
create table if not exists public.units (
    id           uuid primary key default gen_random_uuid(),
    company_id   uuid        not null references public.companies(id) on delete cascade,
    name         text        not null,                 -- e.g. "Kilogram"
    abbreviation text,                                  -- e.g. "Kg" — shown in dropdowns if set
    created_by   uuid        not null references public.users(id) on delete restrict,
    created_at   timestamptz not null default now(),
    unique (company_id, name)
);
create index if not exists units_company_idx on public.units(company_id);

-- ============ inventory: image ============
alter table public.inventory add column if not exists image_url text;

-- ============ Row Level Security ============
alter table public.customers enable row level security;
alter table public.units     enable row level security;
