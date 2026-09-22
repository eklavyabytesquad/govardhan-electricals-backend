-- Migration 004: invoicing — inventory, invoice_series, invoice_config,
-- invoices, invoice_items.
-- Every table is scoped by company_id, tracks created_by/updated_by and
-- timestamps, and supports manual invoice numbers alongside series-based
-- auto-numbering (next_invoice_number() below).
-- Safe to run on your existing database.
--
-- Run in Supabase Dashboard -> SQL Editor.

-- ============ invoice_series ============
-- Numbering series per company (e.g. "INV/24-25/0001"). next_number
-- auto-increments via next_invoice_number(), which is safe under
-- concurrent invoice creation (atomic UPDATE ... RETURNING).
create table if not exists public.invoice_series (
    id           uuid primary key default gen_random_uuid(),
    company_id   uuid        not null references public.companies(id) on delete cascade,
    name         text        not null,                  -- e.g. "Default", "Retail", "Export"
    prefix       text        not null default '',        -- e.g. "INV/24-25/"
    suffix       text        not null default '',
    next_number  integer     not null default 1,
    padding      integer     not null default 4,         -- 4 -> 0001
    is_default   boolean     not null default false,
    created_by   uuid        not null references public.users(id) on delete restrict,
    updated_by   uuid        references public.users(id) on delete set null,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    unique (company_id, name)
);
create index if not exists invoice_series_company_idx on public.invoice_series(company_id);

-- ============ invoice_config ============
-- One row per company: invoice template, permanent/legal details, GSTIN,
-- registered address, and bank/account details shown on every invoice.
create table if not exists public.invoice_config (
    id                   uuid primary key default gen_random_uuid(),
    company_id           uuid        not null unique references public.companies(id) on delete cascade,

    -- template
    template_name        text        not null default 'default',
    logo_url             text,
    theme_color          text,
    notes                text,                          -- default terms & conditions / footer text

    -- permanent / legal details
    legal_name           text,
    pan                  text,
    cin                  text,

    -- GSTIN details
    gstin                text,
    gstin_state          text,
    gstin_state_code     text,

    -- registered address
    address_line1        text,
    address_line2        text,
    city                 text,
    state                text,
    pincode              text,
    country              text        not null default 'India',

    -- bank / account details
    bank_name            text,
    bank_account_name    text,
    bank_account_number  text,
    bank_ifsc            text,
    bank_branch          text,
    upi_id               text,

    default_series_id    uuid        references public.invoice_series(id) on delete set null,

    created_by           uuid        not null references public.users(id) on delete restrict,
    updated_by            uuid        references public.users(id) on delete set null,
    created_at            timestamptz not null default now(),
    updated_at             timestamptz not null default now()
);

-- ============ inventory ============
create table if not exists public.inventory (
    id              uuid primary key default gen_random_uuid(),
    company_id      uuid        not null references public.companies(id) on delete cascade,
    sku             text,
    name            text        not null,
    description     text,
    hsn_code        text,                                -- HSN/SAC code for GST
    unit            text        not null default 'Nos',  -- Nos, Pcs, Mtr, Kg...
    price           numeric(12,2) not null default 0,
    tax_rate        numeric(5,2)  not null default 0,     -- GST % e.g. 18.00
    stock_quantity  numeric(12,2) not null default 0,
    reorder_level   numeric(12,2) not null default 0,
    category        text,
    metadata        jsonb       not null default '{}'::jsonb,
    created_by      uuid        not null references public.users(id) on delete restrict,
    updated_by      uuid        references public.users(id) on delete set null,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    unique (company_id, sku)
);
create index if not exists inventory_company_idx on public.inventory(company_id);

-- ============ invoices ============
create table if not exists public.invoices (
    id                uuid primary key default gen_random_uuid(),
    company_id        uuid        not null references public.companies(id) on delete cascade,
    series_id         uuid        references public.invoice_series(id) on delete set null,
    invoice_number    text        not null,              -- final number shown on the invoice — typed manually, or generated from series_id via next_invoice_number()
    is_manual_number  boolean     not null default false,
    invoice_date      date        not null default current_date,
    due_date          date,
    status            text        not null default 'draft'
                        check (status in ('draft','sent','paid','partially_paid','overdue','cancelled')),

    -- bill-to details (kept on the invoice itself; no separate customers table yet)
    customer_name     text        not null,
    customer_gstin    text,
    customer_phone    text,
    customer_email    text,
    customer_address  text,

    currency          text        not null default 'INR',
    subtotal          numeric(12,2) not null default 0,
    discount_total    numeric(12,2) not null default 0,
    tax_total         numeric(12,2) not null default 0,
    grand_total       numeric(12,2) not null default 0,
    amount_paid       numeric(12,2) not null default 0,

    notes             text,
    terms             text,
    metadata          jsonb       not null default '{}'::jsonb,

    created_by        uuid        not null references public.users(id) on delete restrict,
    updated_by         uuid        references public.users(id) on delete set null,
    created_at         timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    unique (company_id, invoice_number)
);
create index if not exists invoices_company_idx on public.invoices(company_id);
create index if not exists invoices_series_idx  on public.invoices(series_id);
create index if not exists invoices_status_idx  on public.invoices(company_id, status);

-- ============ invoice_items (line items) ============
create table if not exists public.invoice_items (
    id                uuid primary key default gen_random_uuid(),
    invoice_id        uuid        not null references public.invoices(id) on delete cascade,
    inventory_id      uuid        references public.inventory(id) on delete set null,  -- null = free-text line item
    description       text        not null,
    hsn_code          text,
    quantity          numeric(12,2) not null default 1,
    unit              text        not null default 'Nos',
    unit_price        numeric(12,2) not null default 0,
    discount_percent  numeric(5,2)  not null default 0,
    tax_rate          numeric(5,2)  not null default 0,
    tax_amount        numeric(12,2) not null default 0,
    line_total        numeric(12,2) not null default 0,
    sort_order        integer     not null default 0,
    created_at        timestamptz not null default now()
);
create index if not exists invoice_items_invoice_idx on public.invoice_items(invoice_id);

-- ============ series-wise atomic numbering ============
-- Claims the next number in a series and returns the formatted invoice
-- number (prefix + zero-padded number + suffix). Safe under concurrent
-- invoice creation — the increment and the read happen in one atomic
-- UPDATE ... RETURNING, so two requests can never get the same number.
create or replace function public.next_invoice_number(p_series_id uuid)
returns text
language plpgsql
as $$
declare
    v_prefix  text;
    v_suffix  text;
    v_number  integer;
    v_padding integer;
begin
    update public.invoice_series
       set next_number = next_number + 1,
           updated_at  = now()
     where id = p_series_id
       returning prefix, suffix, next_number - 1, padding
       into v_prefix, v_suffix, v_number, v_padding;

    if not found then
        raise exception 'Invoice series % not found', p_series_id;
    end if;

    return v_prefix || lpad(v_number::text, v_padding, '0') || v_suffix;
end;
$$;

-- ============ Row Level Security ============
-- No public policies — only the backend's secret/service_role key can
-- read or write these tables, same as every other private table.
alter table public.invoice_series enable row level security;
alter table public.invoice_config enable row level security;
alter table public.inventory      enable row level security;
alter table public.invoices       enable row level security;
alter table public.invoice_items  enable row level security;
