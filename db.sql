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
alter table public.enquiries       enable row level security;

-- Anyone may submit an enquiry, but nobody can read them with the public key.
drop policy if exists "anyone can submit enquiry" on public.enquiries;
create policy "anyone can submit enquiry" on public.enquiries
    for insert to anon, authenticated with check (true);
