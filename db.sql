-- Govardhan Electricals — run this once in Supabase Dashboard -> SQL Editor.

-- ============ users ============
create table if not exists public.users (
    id            uuid primary key default gen_random_uuid(),
    name          text        not null,
    country_code  text        not null default '91',
    number        text        not null,             -- mobile number without country code
    username      text        not null unique,      -- stored lowercase
    password_hash text        not null,             -- PBKDF2 hash, never plain text
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    unique (country_code, number)
);

-- ============ sessions ============
create table if not exists public.sessions (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid        not null references public.users(id) on delete cascade,
    ip_address  text,
    user_agent  text,
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null,
    revoked_at  timestamptz
);
create index if not exists sessions_user_idx on public.sessions(user_id);

-- ============ tokens (login access tokens + password-reset tokens; only SHA-256 hashes stored) ============
create table if not exists public.tokens (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid        not null references public.users(id) on delete cascade,
    session_id  uuid        references public.sessions(id) on delete cascade,
    token_hash  text        not null unique,
    type        text        not null check (type in ('access', 'password_reset')),
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null,
    revoked_at  timestamptz
);
create index if not exists tokens_user_idx on public.tokens(user_id);

-- ============ otp_records (verification codes sent through the template API) ============
create table if not exists public.otp_records (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid        not null references public.users(id) on delete cascade,
    purpose       text        not null default 'password_reset',
    otp_hash      text        not null,
    attempts      int         not null default 0,
    max_attempts  int         not null default 5,
    created_at    timestamptz not null default now(),
    expires_at    timestamptz not null,
    consumed_at   timestamptz
);
create index if not exists otp_user_idx on public.otp_records(user_id, purpose, created_at desc);

-- ============ companies ============
create table if not exists public.companies (
    id          uuid primary key default gen_random_uuid(),
    name        text        not null,
    gstin       text,                                  -- 15-char Indian GST number, optional
    owner_name  text,
    number      text,                                  -- company contact number
    metadata    jsonb       not null default '{}'::jsonb,  -- free-form extra fields
    created_by  uuid        not null references public.users(id) on delete restrict,
    updated_by  uuid        references public.users(id) on delete set null,
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
    image_url       text,
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

-- ============ invoice search ============
-- Matches invoice number, customer name, and line-item descriptions (a join
-- PostgREST can't express directly), plus date/amount ranges — always
-- scoped to one company. Callable via RPC: POST /rest/v1/rpc/search_invoices.
create or replace function public.search_invoices(
    p_company_id uuid,
    p_query      text default null,
    p_date_from  date default null,
    p_date_to    date default null,
    p_min_amount numeric default null,
    p_max_amount numeric default null
)
returns setof public.invoices
language sql
stable
as $$
    select distinct i.*
    from public.invoices i
    left join public.invoice_items it on it.invoice_id = i.id
    where i.company_id = p_company_id
      and (p_query is null or p_query = '' or (
            i.invoice_number ilike '%' || p_query || '%'
         or i.customer_name  ilike '%' || p_query || '%'
         or it.description   ilike '%' || p_query || '%'
      ))
      and (p_date_from  is null or i.invoice_date >= p_date_from)
      and (p_date_to    is null or i.invoice_date <= p_date_to)
      and (p_min_amount is null or i.grand_total  >= p_min_amount)
      and (p_max_amount is null or i.grand_total  <= p_max_amount)
    order by i.created_at desc;
$$;

-- ============ customers ============
-- A saved customer list per company, so invoices don't need re-typing
-- customer details every time.
create table if not exists public.customers (
    id          uuid primary key default gen_random_uuid(),
    company_id  uuid        not null references public.companies(id) on delete cascade,
    name        text        not null,
    number      text,
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
-- Company-defined unit types (Kg, Dozen, Pcs, Sets, Kits, Bundle, ...).
create table if not exists public.units (
    id           uuid primary key default gen_random_uuid(),
    company_id   uuid        not null references public.companies(id) on delete cascade,
    name         text        not null,
    abbreviation text,
    created_by   uuid        not null references public.users(id) on delete restrict,
    created_at   timestamptz not null default now(),
    unique (company_id, name)
);
create index if not exists units_company_idx on public.units(company_id);

-- ============ enquiries (contact form) ============
create table if not exists public.enquiries (
    id          bigint generated always as identity primary key,
    name        text not null,
    phone       text not null,
    email       text,
    message     text not null,
    created_at  timestamptz not null default now()
);

-- ============ Row Level Security ============
-- No policies on the private tables => the public/publishable key cannot touch them.
-- The Python backend must use the secret (service_role) key, which bypasses RLS.
alter table public.users           enable row level security;
alter table public.sessions        enable row level security;
alter table public.tokens          enable row level security;
alter table public.otp_records     enable row level security;
alter table public.companies       enable row level security;
alter table public.company_members enable row level security;
alter table public.invoice_series  enable row level security;
alter table public.invoice_config  enable row level security;
alter table public.inventory       enable row level security;
alter table public.invoices        enable row level security;
alter table public.invoice_items   enable row level security;
alter table public.customers       enable row level security;
alter table public.units           enable row level security;
alter table public.enquiries       enable row level security;

-- Anyone may submit an enquiry, but nobody can read them with the public key.
drop policy if exists "anyone can submit enquiry" on public.enquiries;
create policy "anyone can submit enquiry" on public.enquiries
    for insert to anon, authenticated with check (true);
