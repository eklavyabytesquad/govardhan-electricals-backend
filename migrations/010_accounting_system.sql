-- Migration 010: multi-company Tally-style accounting — purchases, sales,
-- returns, payments, batch/godown-tracked stock, GST and P&L — built on top
-- of the existing users/companies/company_members tables (not touched here).
--
-- ============================================================================
-- RULES THE APPLICATION LAYER MUST FOLLOW (this migration cannot enforce all
-- of these by itself):
--   1. Every query filters by company_id, taken from the logged-in user's
--      company_members row. Never trust a company_id sent by the client.
--   2. Saving a voucher = one DB transaction: header + items + ledger entries
--      + bill allocations + number-series increment, all together.
--      IMPORTANT: this backend talks to Postgres over Supabase PostgREST,
--      where every HTTP call is its own transaction. The deferred balance
--      trigger below only passes if a voucher's ledger_entries rows all
--      arrive in one transaction — which plain REST calls cannot do. You
--      need a `save_voucher(jsonb)` RPC function (one call, one transaction)
--      before this schema can be used safely from the app. It is NOT built
--      in this migration — that is the required next step.
--   3. Batch-tracked items must have batch_id set on every voucher line —
--      enforced below by trg_voucher_items_batch_required.
--   4. Never allow negative stock on qty_out — enforce in the app
--      (optionally promote to a DB check later if a company setting exists).
--   5. Vouchers in a closed financial year can't be inserted, edited or
--      cancelled — enforced below on vouchers, voucher_items, ledger_entries
--      and bill_allocations (editing = delete + re-insert lines, so the
--      lock has to cover the line tables too, not just the header).
--   6. Editing a voucher = delete its lines and re-insert them in one
--      transaction. If a purchase is edited, recalculate cost_rate on later
--      sales of that item in the app layer.
--
-- IMPORTANT — NOT safe to re-run: unlike earlier migrations in this project,
-- this file does NOT use "create table if not exists". If you run it twice,
-- or run it after only partially applying it, it will error out loudly
-- instead of silently skipping tables — with composite foreign keys here,
-- a silently-skipped table is worse than a loud failure. Run it once.
--
-- Run in Supabase Dashboard -> SQL Editor.
-- ============================================================================

begin;

-- Guard: the OLD public.units table (sku/invoice "unit" picklist, with an
-- `abbreviation` column) must be gone before this migration can create the
-- NEW public.units table (accounting base units, with `decimal_places`).
-- Run migrations/009_remove_invoicing.sql first if this fires.
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'units' and column_name = 'abbreviation'
  ) then
    raise exception 'Run migrations/009_remove_invoicing.sql first — the old public.units table (abbreviation-based) still exists and must be dropped before 010 can create the new accounting units table.';
  end if;
end $$;

-- ============================================================================
-- Shared trigger: keep updated_at current on every table that has one.
-- ============================================================================
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ============================================================================
-- A. SETUP TABLES
-- ============================================================================

-- ---- 1. financial_years ----
create table public.financial_years (
  id            uuid primary key default gen_random_uuid(),
  company_id    uuid not null references public.companies(id) on delete cascade,
  name          text not null,
  start_date    date not null,
  end_date      date not null,
  is_closed     boolean not null default false,
  created_by    uuid not null references public.users(id) on delete restrict,
  updated_by    uuid references public.users(id) on delete set null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint financial_years_dates_chk check (end_date > start_date),
  constraint financial_years_company_id_id_key unique (company_id, id),
  constraint financial_years_company_name_key unique (company_id, name)
);
create index financial_years_company_idx on public.financial_years(company_id);
create trigger trg_financial_years_updated_at before update on public.financial_years
  for each row execute function public.set_updated_at();
alter table public.financial_years enable row level security;

-- ---- 2. account_groups (tree) ----
create table public.account_groups (
  id                     uuid primary key default gen_random_uuid(),
  company_id             uuid not null references public.companies(id) on delete cascade,
  name                   text not null,
  parent_id              uuid,
  nature                 text not null check (nature in ('asset','liability','income','expense')),
  affects_gross_profit   boolean not null default false,
  is_system              boolean not null default false,
  created_by             uuid not null references public.users(id) on delete restrict,
  updated_by             uuid references public.users(id) on delete set null,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  constraint account_groups_company_id_id_key unique (company_id, id),
  constraint account_groups_company_name_key unique (company_id, name),
  constraint account_groups_parent_fkey foreign key (company_id, parent_id)
    references public.account_groups(company_id, id) on delete restrict
);
create index account_groups_company_idx on public.account_groups(company_id);
create index account_groups_parent_idx on public.account_groups(company_id, parent_id);
create trigger trg_account_groups_updated_at before update on public.account_groups
  for each row execute function public.set_updated_at();
alter table public.account_groups enable row level security;

-- ---- 3. ledgers ----
create table public.ledgers (
  id                 uuid primary key default gen_random_uuid(),
  company_id         uuid not null references public.companies(id) on delete cascade,
  name               text not null,
  group_id           uuid not null,
  opening_balance    numeric(14,2) not null default 0,
  opening_type       text not null default 'Dr' check (opening_type in ('Dr','Cr')),
  gstin              text,
  pan                text,
  state_code         text,
  address            text,
  phone              text,
  email              text,
  credit_days        int,
  credit_limit       numeric(14,2),
  track_bills        boolean not null default false,
  system_role        text,
  is_active          boolean not null default true,
  created_by         uuid not null references public.users(id) on delete restrict,
  updated_by         uuid references public.users(id) on delete set null,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  constraint ledgers_company_id_id_key unique (company_id, id),
  constraint ledgers_company_name_key unique (company_id, name),
  constraint ledgers_group_fkey foreign key (company_id, group_id)
    references public.account_groups(company_id, id) on delete restrict
);
create unique index ledgers_company_system_role_key on public.ledgers(company_id, system_role) where system_role is not null;
create index ledgers_company_idx on public.ledgers(company_id);
create index ledgers_group_idx on public.ledgers(company_id, group_id);
create trigger trg_ledgers_updated_at before update on public.ledgers
  for each row execute function public.set_updated_at();
alter table public.ledgers enable row level security;

-- ---- 4. units (accounting base units — NOT the old invoicing picklist) ----
create table public.units (
  id              uuid primary key default gen_random_uuid(),
  company_id      uuid not null references public.companies(id) on delete cascade,
  name            text not null,
  decimal_places  int not null default 0,
  created_by      uuid not null references public.users(id) on delete restrict,
  updated_by      uuid references public.users(id) on delete set null,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  constraint units_company_id_id_key unique (company_id, id),
  constraint units_company_name_key unique (company_id, name)
);
create index units_company_idx on public.units(company_id);
create trigger trg_units_updated_at before update on public.units
  for each row execute function public.set_updated_at();
alter table public.units enable row level security;

-- ---- 5. item_groups (tree) ----
create table public.item_groups (
  id            uuid primary key default gen_random_uuid(),
  company_id    uuid not null references public.companies(id) on delete cascade,
  name          text not null,
  parent_id     uuid,
  created_by    uuid not null references public.users(id) on delete restrict,
  updated_by    uuid references public.users(id) on delete set null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint item_groups_company_id_id_key unique (company_id, id),
  constraint item_groups_company_name_key unique (company_id, name),
  constraint item_groups_parent_fkey foreign key (company_id, parent_id)
    references public.item_groups(company_id, id) on delete restrict
);
create index item_groups_company_idx on public.item_groups(company_id);
create trigger trg_item_groups_updated_at before update on public.item_groups
  for each row execute function public.set_updated_at();
alter table public.item_groups enable row level security;

-- ---- 6. items ----
create table public.items (
  id                      uuid primary key default gen_random_uuid(),
  company_id              uuid not null references public.companies(id) on delete cascade,
  name                    text not null,
  sku                     text,
  group_id                uuid,
  unit_id                 uuid not null,
  hsn_code                text,
  gst_rate                numeric(5,2) not null default 0,
  track_batches           boolean not null default false,
  track_expiry            boolean not null default false,
  reorder_level           numeric(14,3),
  default_sale_rate       numeric(14,4),
  default_purchase_rate   numeric(14,4),
  is_active               boolean not null default true,
  created_by              uuid not null references public.users(id) on delete restrict,
  updated_by              uuid references public.users(id) on delete set null,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  constraint items_company_id_id_key unique (company_id, id),
  constraint items_company_name_key unique (company_id, name),
  constraint items_group_fkey foreign key (company_id, group_id)
    references public.item_groups(company_id, id) on delete restrict,
  constraint items_unit_fkey foreign key (company_id, unit_id)
    references public.units(company_id, id) on delete restrict
);
create unique index items_company_sku_key on public.items(company_id, sku) where sku is not null;
create index items_company_idx on public.items(company_id);
create trigger trg_items_updated_at before update on public.items
  for each row execute function public.set_updated_at();
alter table public.items enable row level security;

-- ---- 7. godowns ----
create table public.godowns (
  id            uuid primary key default gen_random_uuid(),
  company_id    uuid not null references public.companies(id) on delete cascade,
  name          text not null,
  address       text,
  is_default    boolean not null default false,
  created_by    uuid not null references public.users(id) on delete restrict,
  updated_by    uuid references public.users(id) on delete set null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint godowns_company_id_id_key unique (company_id, id),
  constraint godowns_company_name_key unique (company_id, name)
);
create unique index godowns_company_default_key on public.godowns(company_id) where is_default;
create index godowns_company_idx on public.godowns(company_id);
create trigger trg_godowns_updated_at before update on public.godowns
  for each row execute function public.set_updated_at();
alter table public.godowns enable row level security;

-- ---- 8. batches ----
create table public.batches (
  id            uuid primary key default gen_random_uuid(),
  company_id    uuid not null references public.companies(id) on delete cascade,
  item_id       uuid not null,
  batch_no      text not null,
  mfg_date      date,
  expiry_date   date,
  created_by    uuid not null references public.users(id) on delete restrict,
  updated_by    uuid references public.users(id) on delete set null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint batches_company_id_id_key unique (company_id, id),
  -- lets voucher_items pin a batch to the SAME item with a 3-column FK below
  constraint batches_company_item_id_key unique (company_id, item_id, id),
  constraint batches_company_item_batchno_key unique (company_id, item_id, batch_no),
  constraint batches_item_fkey foreign key (company_id, item_id)
    references public.items(company_id, id) on delete restrict
);
create index batches_company_idx on public.batches(company_id);
create index batches_item_idx on public.batches(company_id, item_id);
create trigger trg_batches_updated_at before update on public.batches
  for each row execute function public.set_updated_at();
alter table public.batches enable row level security;

-- ---- 9. voucher_types ----
create table public.voucher_types (
  id                  uuid primary key default gen_random_uuid(),
  company_id          uuid not null references public.companies(id) on delete cascade,
  name                text not null,
  code                text not null check (code in (
    'purchase','sales','payment','receipt','credit_note','debit_note',
    'journal','contra','stock_transfer','opening_stock','stock_adjustment',
    'purchase_order','sales_order','receipt_note','delivery_note'
  )),
  stock_effect        text not null check (stock_effect in ('in','out','both','none')),
  affects_accounts    boolean not null default true,
  prefix              text not null default '',
  created_by          uuid not null references public.users(id) on delete restrict,
  updated_by          uuid references public.users(id) on delete set null,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  constraint voucher_types_company_id_id_key unique (company_id, id),
  constraint voucher_types_company_name_key unique (company_id, name)
);
create index voucher_types_company_idx on public.voucher_types(company_id);
create trigger trg_voucher_types_updated_at before update on public.voucher_types
  for each row execute function public.set_updated_at();
alter table public.voucher_types enable row level security;

-- ---- 10. voucher_number_series ----
create table public.voucher_number_series (
  id                  uuid primary key default gen_random_uuid(),
  company_id          uuid not null references public.companies(id) on delete cascade,
  voucher_type_id     uuid not null,
  financial_year_id   uuid not null,
  next_number         int not null default 1,
  created_by          uuid not null references public.users(id) on delete restrict,
  updated_by          uuid references public.users(id) on delete set null,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  constraint voucher_number_series_company_id_id_key unique (company_id, id),
  constraint voucher_number_series_unique_key unique (company_id, voucher_type_id, financial_year_id),
  constraint voucher_number_series_type_fkey foreign key (company_id, voucher_type_id)
    references public.voucher_types(company_id, id) on delete restrict,
  constraint voucher_number_series_fy_fkey foreign key (company_id, financial_year_id)
    references public.financial_years(company_id, id) on delete restrict
);
create index voucher_number_series_company_idx on public.voucher_number_series(company_id);
create trigger trg_voucher_number_series_updated_at before update on public.voucher_number_series
  for each row execute function public.set_updated_at();
alter table public.voucher_number_series enable row level security;

-- ============================================================================
-- B. ENTRY TABLES
-- ============================================================================

-- ---- 11. vouchers (bill header) ----
create table public.vouchers (
  id                  uuid primary key default gen_random_uuid(),
  company_id          uuid not null references public.companies(id) on delete cascade,
  voucher_type_id     uuid not null,
  financial_year_id   uuid not null,
  voucher_no          text not null,
  date                date not null,
  party_ledger_id     uuid,
  reference_no        text,
  reference_date      date,
  place_of_supply     text,
  narration           text,
  total_amount        numeric(14,2) not null default 0,
  status              text not null default 'active' check (status in ('active','cancelled')),
  cancelled_at        timestamptz,
  cancelled_by        uuid references public.users(id) on delete set null,
  cancel_reason       text,
  created_by          uuid not null references public.users(id) on delete restrict,
  updated_by          uuid references public.users(id) on delete set null,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  constraint vouchers_company_id_id_key unique (company_id, id),
  constraint vouchers_unique_number_key unique (company_id, voucher_type_id, financial_year_id, voucher_no),
  constraint vouchers_type_fkey foreign key (company_id, voucher_type_id)
    references public.voucher_types(company_id, id) on delete restrict,
  constraint vouchers_fy_fkey foreign key (company_id, financial_year_id)
    references public.financial_years(company_id, id) on delete restrict,
  constraint vouchers_party_fkey foreign key (company_id, party_ledger_id)
    references public.ledgers(company_id, id) on delete restrict
);
create index vouchers_company_date_idx on public.vouchers(company_id, date);
create index vouchers_company_party_idx on public.vouchers(company_id, party_ledger_id);
create index vouchers_company_type_date_idx on public.vouchers(company_id, voucher_type_id, date);
create trigger trg_vouchers_updated_at before update on public.vouchers
  for each row execute function public.set_updated_at();
alter table public.vouchers enable row level security;

-- ---- 12. voucher_items (product lines = the inventory) ----
-- No updated_at/updated_by: a line is never edited in place — rule 6 above
-- says editing a voucher means deleting and re-inserting all of its lines.
create table public.voucher_items (
  id                  uuid primary key default gen_random_uuid(),
  company_id          uuid not null references public.companies(id) on delete cascade,
  voucher_id          uuid not null,
  line_no             int not null,
  item_id             uuid not null,
  godown_id           uuid not null,
  batch_id            uuid,
  qty_in              numeric(14,3) not null default 0,
  qty_out             numeric(14,3) not null default 0,
  rate                numeric(14,4) not null default 0,
  discount_amount     numeric(14,2) not null default 0,
  taxable_amount       numeric(14,2) not null default 0,
  gst_rate            numeric(5,2) not null default 0,
  cgst                numeric(14,2) not null default 0,
  sgst                numeric(14,2) not null default 0,
  igst                numeric(14,2) not null default 0,
  cess                numeric(14,2) not null default 0,
  line_total          numeric(14,2) not null default 0,
  cost_rate           numeric(14,4),
  created_at          timestamptz not null default now(),
  constraint voucher_items_company_id_id_key unique (company_id, id),
  constraint voucher_items_qty_chk check (
    qty_in >= 0 and qty_out >= 0 and
    ((qty_in > 0 and qty_out = 0) or (qty_out > 0 and qty_in = 0))
  ),
  constraint voucher_items_voucher_fkey foreign key (company_id, voucher_id)
    references public.vouchers(company_id, id) on delete cascade,
  constraint voucher_items_item_fkey foreign key (company_id, item_id)
    references public.items(company_id, id) on delete restrict,
  constraint voucher_items_godown_fkey foreign key (company_id, godown_id)
    references public.godowns(company_id, id) on delete restrict,
  -- 3-column FK: makes it impossible for a line to pin a batch that belongs
  -- to a DIFFERENT item than the line's own item_id.
  constraint voucher_items_item_batch_fkey foreign key (company_id, item_id, batch_id)
    references public.batches(company_id, item_id, id) on delete restrict
);
create index voucher_items_company_idx on public.voucher_items(company_id, item_id, godown_id, batch_id);
create index voucher_items_voucher_idx on public.voucher_items(company_id, voucher_id);
create index voucher_items_batch_idx on public.voucher_items(company_id, batch_id);
alter table public.voucher_items enable row level security;

-- ---- 13. ledger_entries (money lines: debit/credit) ----
create table public.ledger_entries (
  id            uuid primary key default gen_random_uuid(),
  company_id    uuid not null references public.companies(id) on delete cascade,
  voucher_id    uuid not null,
  ledger_id     uuid not null,
  debit         numeric(14,2) not null default 0,
  credit        numeric(14,2) not null default 0,
  created_at    timestamptz not null default now(),
  constraint ledger_entries_company_id_id_key unique (company_id, id),
  constraint ledger_entries_amount_chk check (
    debit >= 0 and credit >= 0 and
    ((debit > 0 and credit = 0) or (credit > 0 and debit = 0))
  ),
  constraint ledger_entries_voucher_fkey foreign key (company_id, voucher_id)
    references public.vouchers(company_id, id) on delete cascade,
  constraint ledger_entries_ledger_fkey foreign key (company_id, ledger_id)
    references public.ledgers(company_id, id) on delete restrict
);
create index ledger_entries_company_ledger_idx on public.ledger_entries(company_id, ledger_id);
create index ledger_entries_voucher_idx on public.ledger_entries(company_id, voucher_id);
alter table public.ledger_entries enable row level security;

-- Deferred balance check: fires once per changed row, but only actually runs
-- at COMMIT (deferred), by which point every line of the voucher has already
-- been inserted — so it can safely sum the whole voucher in one go.
create or replace function public.check_voucher_balanced()
returns trigger
language plpgsql
as $$
declare
  v_voucher_id uuid;
  v_diff numeric(14,2);
begin
  v_voucher_id := coalesce(new.voucher_id, old.voucher_id);
  select coalesce(sum(debit), 0) - coalesce(sum(credit), 0)
    into v_diff
    from public.ledger_entries
   where voucher_id = v_voucher_id;
  if v_diff <> 0 then
    raise exception 'Voucher % is not balanced: debit/credit differ by %', v_voucher_id, v_diff;
  end if;
  return null;
end;
$$;

create constraint trigger trg_ledger_entries_balanced
  after insert or update or delete on public.ledger_entries
  deferrable initially deferred
  for each row execute function public.check_voucher_balanced();

-- ---- 14. bill_allocations (who owes what) ----
create table public.bill_allocations (
  id                uuid primary key default gen_random_uuid(),
  company_id        uuid not null references public.companies(id) on delete cascade,
  voucher_id        uuid not null,
  ledger_id         uuid not null,
  ref_type          text not null check (ref_type in ('new','against','advance','on_account')),
  bill_voucher_id   uuid,
  bill_ref          text,
  due_date          date,
  amount            numeric(14,2) not null,
  created_at        timestamptz not null default now(),
  constraint bill_allocations_company_id_id_key unique (company_id, id),
  constraint bill_allocations_voucher_fkey foreign key (company_id, voucher_id)
    references public.vouchers(company_id, id) on delete cascade,
  constraint bill_allocations_ledger_fkey foreign key (company_id, ledger_id)
    references public.ledgers(company_id, id) on delete restrict,
  constraint bill_allocations_bill_voucher_fkey foreign key (company_id, bill_voucher_id)
    references public.vouchers(company_id, id) on delete restrict
);
create index bill_allocations_company_ledger_idx on public.bill_allocations(company_id, ledger_id);
create index bill_allocations_bill_voucher_idx on public.bill_allocations(company_id, bill_voucher_id);
alter table public.bill_allocations enable row level security;

-- ---- 15. voucher_links (order -> delivery -> invoice) ----
create table public.voucher_links (
  id                uuid primary key default gen_random_uuid(),
  company_id        uuid not null references public.companies(id) on delete cascade,
  from_voucher_id   uuid not null,
  to_voucher_id     uuid not null,
  item_id           uuid,
  qty               numeric(14,3),
  created_at        timestamptz not null default now(),
  constraint voucher_links_company_id_id_key unique (company_id, id),
  constraint voucher_links_from_fkey foreign key (company_id, from_voucher_id)
    references public.vouchers(company_id, id) on delete cascade,
  constraint voucher_links_to_fkey foreign key (company_id, to_voucher_id)
    references public.vouchers(company_id, id) on delete cascade,
  constraint voucher_links_item_fkey foreign key (company_id, item_id)
    references public.items(company_id, id) on delete restrict
);
create index voucher_links_from_idx on public.voucher_links(company_id, from_voucher_id);
create index voucher_links_to_idx on public.voucher_links(company_id, to_voucher_id);
alter table public.voucher_links enable row level security;

-- ============================================================================
-- C. CONSTRAINT TRIGGERS (business rules the FKs/CHECKs above can't express)
-- ============================================================================

-- Rule 3: batch-tracked items must have batch_id on every line.
create or replace function public.check_voucher_item_batch()
returns trigger
language plpgsql
as $$
declare
  v_track_batches boolean;
begin
  select track_batches into v_track_batches
    from public.items
   where company_id = new.company_id and id = new.item_id;

  if v_track_batches and new.batch_id is null then
    raise exception 'Item % tracks batches — batch_id is required on every voucher line', new.item_id;
  end if;
  return new;
end;
$$;

create trigger trg_voucher_items_batch_required
  before insert or update on public.voucher_items
  for each row execute function public.check_voucher_item_batch();

-- Rule 5: vouchers in a closed financial year can't be inserted, edited or
-- cancelled. Covers the header directly, and the three line tables by
-- looking up the parent voucher's financial year.
create or replace function public.check_fy_open_for_vouchers_table()
returns trigger
language plpgsql
as $$
declare
  v_closed      boolean;
  v_fy_id       uuid;
  v_company_id  uuid;
begin
  v_company_id := coalesce(new.company_id, old.company_id);
  v_fy_id := coalesce(new.financial_year_id, old.financial_year_id);

  select is_closed into v_closed
    from public.financial_years
   where company_id = v_company_id and id = v_fy_id;

  if v_closed then
    raise exception 'Financial year is closed — voucher cannot be inserted, edited or cancelled';
  end if;
  return coalesce(new, old);
end;
$$;

create trigger trg_vouchers_fy_lock
  before insert or update or delete on public.vouchers
  for each row execute function public.check_fy_open_for_vouchers_table();

create or replace function public.check_fy_open_for_voucher()
returns trigger
language plpgsql
as $$
declare
  v_company_id  uuid;
  v_voucher_id  uuid;
  v_closed      boolean;
begin
  v_company_id := coalesce(new.company_id, old.company_id);
  v_voucher_id := coalesce(new.voucher_id, old.voucher_id);

  select fy.is_closed into v_closed
    from public.vouchers v
    join public.financial_years fy
      on fy.company_id = v.company_id and fy.id = v.financial_year_id
   where v.company_id = v_company_id and v.id = v_voucher_id;

  if v_closed then
    raise exception 'Voucher % belongs to a closed financial year and cannot be changed', v_voucher_id;
  end if;
  return coalesce(new, old);
end;
$$;

create trigger trg_voucher_items_fy_lock
  before insert or update or delete on public.voucher_items
  for each row execute function public.check_fy_open_for_voucher();

create trigger trg_ledger_entries_fy_lock
  before insert or update or delete on public.ledger_entries
  for each row execute function public.check_fy_open_for_voucher();

create trigger trg_bill_allocations_fy_lock
  before insert or update or delete on public.bill_allocations
  for each row execute function public.check_fy_open_for_voucher();

-- ============================================================================
-- D. DEFAULT DATA FOR EVERY COMPANY
-- ============================================================================

create or replace function public.seed_company_defaults(p_company_id uuid, p_user_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_today   date := current_date;
  v_fy_start date;
  v_fy_end   date;
  v_fy_name  text;
  v_fy_id    uuid;

  v_assets uuid; v_liabilities uuid; v_income uuid; v_expense uuid;
  v_current_assets uuid; v_current_liabilities uuid;
  v_cash_hand uuid; v_bank uuid; v_debtors uuid; v_stock_hand uuid; v_duties_input uuid;
  v_capital uuid; v_loans uuid; v_creditors uuid; v_duties_output uuid;
  v_sales_grp uuid; v_direct_income uuid; v_indirect_income uuid;
  v_purchase_grp uuid; v_direct_expense uuid; v_indirect_expense uuid;
begin
  -- idempotent: if this company already has groups, assume it's fully seeded.
  if exists (select 1 from public.account_groups where company_id = p_company_id) then
    return;
  end if;

  -- ---- financial year containing today (Indian FY: Apr 1 - Mar 31) ----
  if extract(month from v_today) >= 4 then
    v_fy_start := make_date(extract(year from v_today)::int, 4, 1);
    v_fy_end   := make_date(extract(year from v_today)::int + 1, 3, 31);
  else
    v_fy_start := make_date(extract(year from v_today)::int - 1, 4, 1);
    v_fy_end   := make_date(extract(year from v_today)::int, 3, 31);
  end if;
  v_fy_name := extract(year from v_fy_start)::text || '-' ||
               lpad((extract(year from v_fy_end)::int % 100)::text, 2, '0');

  insert into public.financial_years (company_id, name, start_date, end_date, created_by)
  values (p_company_id, v_fy_name, v_fy_start, v_fy_end, p_user_id)
  returning id into v_fy_id;

  -- ---- account groups ----
  insert into public.account_groups (company_id, name, nature, is_system, created_by)
    values (p_company_id, 'Assets', 'asset', true, p_user_id) returning id into v_assets;
  insert into public.account_groups (company_id, name, parent_id, nature, is_system, created_by)
    values (p_company_id, 'Current Assets', v_assets, 'asset', true, p_user_id) returning id into v_current_assets;
  insert into public.account_groups (company_id, name, parent_id, nature, is_system, created_by)
    values (p_company_id, 'Cash-in-Hand', v_current_assets, 'asset', true, p_user_id) returning id into v_cash_hand;
  insert into public.account_groups (company_id, name, parent_id, nature, is_system, created_by)
    values (p_company_id, 'Bank Accounts', v_current_assets, 'asset', true, p_user_id) returning id into v_bank;
  insert into public.account_groups (company_id, name, parent_id, nature, is_system, created_by)
    values (p_company_id, 'Sundry Debtors', v_current_assets, 'asset', true, p_user_id) returning id into v_debtors;
  insert into public.account_groups (company_id, name, parent_id, nature, is_system, created_by)
    values (p_company_id, 'Stock-in-Hand', v_current_assets, 'asset', true, p_user_id) returning id into v_stock_hand;
  insert into public.account_groups (company_id, name, parent_id, nature, is_system, created_by)
    values (p_company_id, 'Duties & Taxes (input)', v_current_assets, 'asset', true, p_user_id) returning id into v_duties_input;

  insert into public.account_groups (company_id, name, nature, is_system, created_by)
    values (p_company_id, 'Liabilities', 'liability', true, p_user_id) returning id into v_liabilities;
  insert into public.account_groups (company_id, name, parent_id, nature, is_system, created_by)
    values (p_company_id, 'Capital Account', v_liabilities, 'liability', true, p_user_id) returning id into v_capital;
  insert into public.account_groups (company_id, name, parent_id, nature, is_system, created_by)
    values (p_company_id, 'Loans', v_liabilities, 'liability', true, p_user_id) returning id into v_loans;
  insert into public.account_groups (company_id, name, parent_id, nature, is_system, created_by)
    values (p_company_id, 'Current Liabilities', v_liabilities, 'liability', true, p_user_id) returning id into v_current_liabilities;
  insert into public.account_groups (company_id, name, parent_id, nature, is_system, created_by)
    values (p_company_id, 'Sundry Creditors', v_current_liabilities, 'liability', true, p_user_id) returning id into v_creditors;
  insert into public.account_groups (company_id, name, parent_id, nature, is_system, created_by)
    values (p_company_id, 'Duties & Taxes', v_current_liabilities, 'liability', true, p_user_id) returning id into v_duties_output;

  insert into public.account_groups (company_id, name, nature, is_system, created_by)
    values (p_company_id, 'Income', 'income', true, p_user_id) returning id into v_income;
  insert into public.account_groups (company_id, name, parent_id, nature, affects_gross_profit, is_system, created_by)
    values (p_company_id, 'Sales Accounts', v_income, 'income', true, true, p_user_id) returning id into v_sales_grp;
  insert into public.account_groups (company_id, name, parent_id, nature, affects_gross_profit, is_system, created_by)
    values (p_company_id, 'Direct Incomes', v_income, 'income', true, true, p_user_id) returning id into v_direct_income;
  insert into public.account_groups (company_id, name, parent_id, nature, is_system, created_by)
    values (p_company_id, 'Indirect Incomes', v_income, 'income', true, p_user_id) returning id into v_indirect_income;

  insert into public.account_groups (company_id, name, nature, is_system, created_by)
    values (p_company_id, 'Expense', 'expense', true, p_user_id) returning id into v_expense;
  insert into public.account_groups (company_id, name, parent_id, nature, affects_gross_profit, is_system, created_by)
    values (p_company_id, 'Purchase Accounts', v_expense, 'expense', true, true, p_user_id) returning id into v_purchase_grp;
  insert into public.account_groups (company_id, name, parent_id, nature, affects_gross_profit, is_system, created_by)
    values (p_company_id, 'Direct Expenses', v_expense, 'expense', true, true, p_user_id) returning id into v_direct_expense;
  insert into public.account_groups (company_id, name, parent_id, nature, is_system, created_by)
    values (p_company_id, 'Indirect Expenses', v_expense, 'expense', true, p_user_id) returning id into v_indirect_expense;

  -- ---- ledgers with system_role ----
  insert into public.ledgers (company_id, name, group_id, system_role, created_by)
    values (p_company_id, 'Cash', v_cash_hand, 'cash', p_user_id);
  insert into public.ledgers (company_id, name, group_id, system_role, created_by)
    values (p_company_id, 'Sales A/c', v_sales_grp, 'sales', p_user_id);
  insert into public.ledgers (company_id, name, group_id, system_role, created_by)
    values (p_company_id, 'Purchase A/c', v_purchase_grp, 'purchase', p_user_id);
  insert into public.ledgers (company_id, name, group_id, system_role, created_by)
    values (p_company_id, 'CGST Input', v_duties_input, 'cgst_input', p_user_id);
  insert into public.ledgers (company_id, name, group_id, system_role, created_by)
    values (p_company_id, 'SGST Input', v_duties_input, 'sgst_input', p_user_id);
  insert into public.ledgers (company_id, name, group_id, system_role, created_by)
    values (p_company_id, 'IGST Input', v_duties_input, 'igst_input', p_user_id);
  insert into public.ledgers (company_id, name, group_id, system_role, created_by)
    values (p_company_id, 'CGST Output', v_duties_output, 'cgst_output', p_user_id);
  insert into public.ledgers (company_id, name, group_id, system_role, created_by)
    values (p_company_id, 'SGST Output', v_duties_output, 'sgst_output', p_user_id);
  insert into public.ledgers (company_id, name, group_id, system_role, created_by)
    values (p_company_id, 'IGST Output', v_duties_output, 'igst_output', p_user_id);
  insert into public.ledgers (company_id, name, group_id, system_role, created_by)
    values (p_company_id, 'Round Off', v_indirect_expense, 'round_off', p_user_id);
  insert into public.ledgers (company_id, name, group_id, system_role, created_by)
    values (p_company_id, 'Discount', v_indirect_income, 'discount', p_user_id);

  -- ---- units ----
  insert into public.units (company_id, name, decimal_places, created_by) values
    (p_company_id, 'pcs', 0, p_user_id),
    (p_company_id, 'kg',  3, p_user_id),
    (p_company_id, 'g',   0, p_user_id),
    (p_company_id, 'ltr', 3, p_user_id),
    (p_company_id, 'box', 0, p_user_id),
    (p_company_id, 'mtr', 2, p_user_id);

  -- ---- godown ----
  insert into public.godowns (company_id, name, is_default, created_by)
    values (p_company_id, 'Main Location', true, p_user_id);

  -- ---- voucher types ----
  insert into public.voucher_types (company_id, name, code, stock_effect, affects_accounts, prefix, created_by) values
    (p_company_id, 'Purchase',         'purchase',         'in',   true,  'PUR/', p_user_id),
    (p_company_id, 'Sales',            'sales',            'out',  true,  'INV/', p_user_id),
    (p_company_id, 'Payment',          'payment',          'none', true,  'PMT/', p_user_id),
    (p_company_id, 'Receipt',          'receipt',          'none', true,  'RCT/', p_user_id),
    (p_company_id, 'Credit Note',      'credit_note',      'in',   true,  'CN/',  p_user_id),
    (p_company_id, 'Debit Note',       'debit_note',       'out',  true,  'DN/',  p_user_id),
    (p_company_id, 'Journal',          'journal',          'none', true,  'JRN/', p_user_id),
    (p_company_id, 'Contra',           'contra',           'none', true,  'CTR/', p_user_id),
    (p_company_id, 'Stock Transfer',   'stock_transfer',   'both', false, 'STK/', p_user_id),
    (p_company_id, 'Opening Stock',    'opening_stock',    'in',   false, 'OPN/', p_user_id),
    (p_company_id, 'Stock Adjustment', 'stock_adjustment', 'both', false, 'ADJ/', p_user_id),
    (p_company_id, 'Purchase Order',   'purchase_order',   'none', false, 'PO/',  p_user_id),
    (p_company_id, 'Sales Order',      'sales_order',      'none', false, 'SO/',  p_user_id),
    (p_company_id, 'Receipt Note',     'receipt_note',     'in',   false, 'GRN/', p_user_id),
    (p_company_id, 'Delivery Note',    'delivery_note',    'out',  false, 'DC/',  p_user_id);

  -- ---- number series for every voucher type, for this financial year ----
  insert into public.voucher_number_series (company_id, voucher_type_id, financial_year_id, created_by)
  select p_company_id, vt.id, v_fy_id, p_user_id
    from public.voucher_types vt
   where vt.company_id = p_company_id;
end;
$$;

revoke execute on function public.seed_company_defaults(uuid, uuid) from public, anon, authenticated;

create or replace function public.trg_seed_company_defaults()
returns trigger
language plpgsql
as $$
begin
  perform public.seed_company_defaults(new.id, new.created_by);
  return new;
end;
$$;

create trigger trg_companies_seed_defaults
  after insert on public.companies
  for each row execute function public.trg_seed_company_defaults();

-- Backfill: seed defaults for every company that already existed before
-- this migration. Safe to run even if some are already seeded (idempotent
-- check inside seed_company_defaults).
do $$
declare
  r record;
begin
  for r in select id, created_by from public.companies loop
    perform public.seed_company_defaults(r.id, r.created_by);
  end loop;
end $$;

-- ============================================================================
-- E. HELPER VIEWS (reports — nothing is stored, everything is computed)
-- ============================================================================

-- Stock on hand per item/godown/batch. Excludes voucher types with
-- stock_effect = 'none' (orders have voucher_items rows too, since the
-- qty check forces one side > 0, but they must NOT move stock) and
-- cancelled vouchers.
create view public.v_stock_balance as
select
  vi.company_id,
  vi.item_id,
  vi.godown_id,
  vi.batch_id,
  sum(vi.qty_in) - sum(vi.qty_out) as balance_qty
from public.voucher_items vi
join public.vouchers v on v.company_id = vi.company_id and v.id = vi.voucher_id
join public.voucher_types vt on vt.company_id = v.company_id and vt.id = v.voucher_type_id
where v.status = 'active'
  and vt.stock_effect <> 'none'
group by vi.company_id, vi.item_id, vi.godown_id, vi.batch_id;

revoke all on public.v_stock_balance from anon, authenticated;

-- Stock value per item at weighted-average purchase/inbound cost.
create view public.v_stock_value as
select
  s.company_id,
  s.item_id,
  s.total_qty,
  s.avg_cost,
  s.total_qty * s.avg_cost as stock_value
from (
  select
    vi.company_id,
    vi.item_id,
    sum(vi.qty_in) - sum(vi.qty_out) as total_qty,
    case when sum(vi.qty_in) > 0
      then sum(vi.qty_in * vi.rate) / sum(vi.qty_in)
      else 0
    end as avg_cost
  from public.voucher_items vi
  join public.vouchers v on v.company_id = vi.company_id and v.id = vi.voucher_id
  join public.voucher_types vt on vt.company_id = v.company_id and vt.id = v.voucher_type_id
  where v.status = 'active' and vt.stock_effect <> 'none'
  group by vi.company_id, vi.item_id
) s;

revoke all on public.v_stock_value from anon, authenticated;

-- Batch stock with expiry, for expiring-soon lists.
create view public.v_batch_expiry as
select
  b.company_id,
  b.item_id,
  b.id as batch_id,
  b.batch_no,
  b.expiry_date,
  coalesce((
    select sum(sb.balance_qty) from public.v_stock_balance sb
     where sb.company_id = b.company_id and sb.item_id = b.item_id and sb.batch_id = b.id
  ), 0) as balance_qty
from public.batches b
where b.expiry_date is not null;

revoke all on public.v_batch_expiry from anon, authenticated;

-- Items below their reorder level.
create view public.v_low_stock as
select
  i.company_id,
  i.id as item_id,
  i.name,
  i.reorder_level,
  coalesce(s.total_qty, 0) as current_qty
from public.items i
left join (
  select company_id, item_id, sum(balance_qty) as total_qty
    from public.v_stock_balance
   group by company_id, item_id
) s on s.company_id = i.company_id and s.item_id = i.id
where i.reorder_level is not null
  and coalesce(s.total_qty, 0) < i.reorder_level;

revoke all on public.v_low_stock from anon, authenticated;

-- Running balance per ledger (opening balance +/- all active-voucher
-- entries). LEFT JOIN from ledgers so a ledger with zero entries still
-- shows its opening balance instead of disappearing; the cancelled-voucher
-- filter lives inside the subquery so it can't turn the LEFT JOIN into an
-- accidental INNER JOIN.
create view public.v_ledger_balance as
select
  l.company_id,
  l.id as ledger_id,
  l.name,
  (case when l.opening_type = 'Dr' then l.opening_balance else -l.opening_balance end)
    + coalesce(le.net, 0) as closing_balance
from public.ledgers l
left join (
  select le.company_id, le.ledger_id, sum(le.debit) - sum(le.credit) as net
    from public.ledger_entries le
    join public.vouchers v on v.company_id = le.company_id and v.id = le.voucher_id
   where v.status = 'active'
   group by le.company_id, le.ledger_id
) le on le.company_id = l.company_id and le.ledger_id = l.id;

revoke all on public.v_ledger_balance from anon, authenticated;

-- Bill-wise outstanding per party: each 'new' bill minus whatever has been
-- settled 'against' it, with due date and days overdue.
create view public.v_bill_outstanding as
select
  n.company_id,
  n.ledger_id,
  n.bill_voucher_id,
  n.bill_ref,
  n.due_date,
  n.amount as bill_amount,
  coalesce(a.settled, 0) as settled_amount,
  n.amount - coalesce(a.settled, 0) as outstanding_amount,
  case when n.due_date is not null and n.due_date < current_date
    then current_date - n.due_date else 0 end as days_overdue
from (
  select ba.company_id, ba.ledger_id, ba.bill_voucher_id, ba.bill_ref, ba.due_date, ba.amount
    from public.bill_allocations ba
    join public.vouchers v on v.company_id = ba.company_id and v.id = ba.voucher_id
   where ba.ref_type = 'new' and v.status = 'active'
) n
left join (
  select ba.company_id, ba.ledger_id, ba.bill_voucher_id, sum(ba.amount) as settled
    from public.bill_allocations ba
    join public.vouchers v on v.company_id = ba.company_id and v.id = ba.voucher_id
   where ba.ref_type = 'against' and v.status = 'active'
   group by ba.company_id, ba.ledger_id, ba.bill_voucher_id
) a on a.company_id = n.company_id and a.ledger_id = n.ledger_id and a.bill_voucher_id = n.bill_voucher_id;

revoke all on public.v_bill_outstanding from anon, authenticated;

-- Per-sale-line profit: (sale rate - cost rate) x qty, less discount.
create view public.v_sales_profit as
select
  vi.company_id,
  vi.voucher_id,
  vi.id as voucher_item_id,
  vi.item_id,
  vi.qty_out,
  vi.rate,
  vi.cost_rate,
  vi.discount_amount,
  (vi.rate - coalesce(vi.cost_rate, 0)) * vi.qty_out - vi.discount_amount as profit
from public.voucher_items vi
join public.vouchers v on v.company_id = vi.company_id and v.id = vi.voucher_id
join public.voucher_types vt on vt.company_id = v.company_id and vt.id = v.voucher_type_id
where vt.code = 'sales' and v.status = 'active' and vi.qty_out > 0;

revoke all on public.v_sales_profit from anon, authenticated;

commit;
