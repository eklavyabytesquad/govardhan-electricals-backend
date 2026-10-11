-- Migration 011: the save_voucher / cancel_voucher RPC functions that
-- migration 010's own comments said were the required next step.
--
-- Why this has to be an RPC and not plain REST inserts: the backend talks to
-- Postgres over Supabase PostgREST, where every HTTP call is its own
-- transaction. A voucher's header + items + ledger entries + bill
-- allocations + number-series increment must land in ONE transaction (rule
-- 2 of migration 010), and the deferred balance trigger on ledger_entries
-- only has something to check once every leg of the voucher has been
-- inserted. A single RPC call is a single Postgres function call, which
-- runs as one transaction — so this is the only way to satisfy both rules
-- from a REST backend.
--
-- Run in Supabase Dashboard -> SQL Editor, after 010_accounting_system.sql.

begin;

create or replace function public.save_voucher(p_payload jsonb)
returns jsonb
language plpgsql
as $$
declare
  v_company_id         uuid := (p_payload->>'company_id')::uuid;
  v_voucher_type_id    uuid := (p_payload->>'voucher_type_id')::uuid;
  v_financial_year_id  uuid := (p_payload->>'financial_year_id')::uuid;
  v_user_id            uuid := (p_payload->>'created_by')::uuid;
  v_date               date := coalesce(nullif(p_payload->>'date', '')::date, current_date);
  v_party_ledger_id    uuid := nullif(p_payload->>'party_ledger_id', '')::uuid;
  v_voucher_no         text := p_payload->>'voucher_no';
  v_prefix             text;
  v_next_number        int;
  v_total              numeric(14,2) := 0;
  v_debit_total        numeric(14,2) := 0;
  v_item_count         int := 0;
  v_voucher_id         uuid;
  v_item               jsonb;
  v_entry              jsonb;
  v_alloc              jsonb;
  v_line_no            int := 0;
begin
  if v_company_id is null or v_voucher_type_id is null or v_financial_year_id is null or v_user_id is null then
    raise exception 'company_id, voucher_type_id, financial_year_id and created_by are all required';
  end if;

  -- Auto-number via the series if the caller didn't supply voucher_no.
  if v_voucher_no is null or v_voucher_no = '' then
    update public.voucher_number_series
       set next_number = next_number + 1, updated_at = now(), updated_by = v_user_id
     where company_id = v_company_id
       and voucher_type_id = v_voucher_type_id
       and financial_year_id = v_financial_year_id
     returning next_number - 1 into v_next_number;

    if v_next_number is null then
      raise exception 'No number series found for this voucher type in this financial year';
    end if;

    select prefix into v_prefix from public.voucher_types where company_id = v_company_id and id = v_voucher_type_id;
    v_voucher_no := coalesce(v_prefix, '') || lpad(v_next_number::text, 4, '0');
  end if;

  insert into public.vouchers (
    company_id, voucher_type_id, financial_year_id, voucher_no, date,
    party_ledger_id, reference_no, reference_date, place_of_supply, narration, created_by
  ) values (
    v_company_id, v_voucher_type_id, v_financial_year_id, v_voucher_no, v_date,
    v_party_ledger_id,
    p_payload->>'reference_no',
    nullif(p_payload->>'reference_date', '')::date,
    p_payload->>'place_of_supply',
    p_payload->>'narration',
    v_user_id
  ) returning id into v_voucher_id;

  for v_item in select * from jsonb_array_elements(coalesce(p_payload->'items', '[]'::jsonb))
  loop
    v_line_no := v_line_no + 1;
    insert into public.voucher_items (
      company_id, voucher_id, line_no, item_id, godown_id, batch_id,
      qty_in, qty_out, rate, discount_amount, taxable_amount, gst_rate,
      cgst, sgst, igst, cess, line_total, cost_rate
    ) values (
      v_company_id, v_voucher_id, v_line_no,
      (v_item->>'item_id')::uuid,
      (v_item->>'godown_id')::uuid,
      nullif(v_item->>'batch_id', '')::uuid,
      coalesce((v_item->>'qty_in')::numeric, 0),
      coalesce((v_item->>'qty_out')::numeric, 0),
      coalesce((v_item->>'rate')::numeric, 0),
      coalesce((v_item->>'discount_amount')::numeric, 0),
      coalesce((v_item->>'taxable_amount')::numeric, 0),
      coalesce((v_item->>'gst_rate')::numeric, 0),
      coalesce((v_item->>'cgst')::numeric, 0),
      coalesce((v_item->>'sgst')::numeric, 0),
      coalesce((v_item->>'igst')::numeric, 0),
      coalesce((v_item->>'cess')::numeric, 0),
      coalesce((v_item->>'line_total')::numeric, 0),
      nullif(v_item->>'cost_rate', '')::numeric
    );
    v_total := v_total + coalesce((v_item->>'line_total')::numeric, 0);
    v_item_count := v_item_count + 1;
  end loop;

  for v_entry in select * from jsonb_array_elements(coalesce(p_payload->'ledger_entries', '[]'::jsonb))
  loop
    insert into public.ledger_entries (company_id, voucher_id, ledger_id, debit, credit)
    values (
      v_company_id, v_voucher_id,
      (v_entry->>'ledger_id')::uuid,
      coalesce((v_entry->>'debit')::numeric, 0),
      coalesce((v_entry->>'credit')::numeric, 0)
    );
    v_debit_total := v_debit_total + coalesce((v_entry->>'debit')::numeric, 0);
  end loop;

  for v_alloc in select * from jsonb_array_elements(coalesce(p_payload->'bill_allocations', '[]'::jsonb))
  loop
    insert into public.bill_allocations (
      company_id, voucher_id, ledger_id, ref_type, bill_voucher_id, bill_ref, due_date, amount
    ) values (
      v_company_id, v_voucher_id,
      (v_alloc->>'ledger_id')::uuid,
      v_alloc->>'ref_type',
      -- A 'new' bill IS this voucher — the caller can't know this voucher's
      -- id before it exists, so default to it. Without this, v_bill_outstanding
      -- (which joins on bill_voucher_id) never matches it, and the bill never
      -- shows as outstanding or gets settled.
      case when v_alloc->>'ref_type' = 'new'
        then coalesce(nullif(v_alloc->>'bill_voucher_id', '')::uuid, v_voucher_id)
        else nullif(v_alloc->>'bill_voucher_id', '')::uuid
      end,
      case when v_alloc->>'ref_type' = 'new'
        then coalesce(nullif(v_alloc->>'bill_ref', ''), v_voucher_no)
        else v_alloc->>'bill_ref'
      end,
      nullif(v_alloc->>'due_date', '')::date,
      coalesce((v_alloc->>'amount')::numeric, 0)
    );
  end loop;

  -- total_amount is a display figure: the sum of item lines for stock
  -- vouchers, or the sum of debits for pure-money vouchers (payment,
  -- receipt, journal...) that have no items at all.
  update public.vouchers
     set total_amount = case when v_item_count > 0 then v_total else v_debit_total end
   where id = v_voucher_id;

  -- Force the deferred balance trigger to run NOW, inside this same
  -- transaction, so an unbalanced voucher fails loudly right here with a
  -- clear error — instead of only surfacing at the implicit commit after
  -- this function returns.
  set constraints trg_ledger_entries_balanced immediate;

  return jsonb_build_object('id', v_voucher_id, 'voucher_no', v_voucher_no, 'total_amount', v_total);
end;
$$;

revoke execute on function public.save_voucher(jsonb) from public, anon, authenticated;

create or replace function public.cancel_voucher(
  p_company_id uuid, p_voucher_id uuid, p_user_id uuid, p_reason text default null
)
returns jsonb
language plpgsql
as $$
declare
  v_id uuid;
begin
  update public.vouchers
     set status = 'cancelled', cancelled_at = now(), cancelled_by = p_user_id,
         cancel_reason = p_reason, updated_by = p_user_id
   where company_id = p_company_id and id = p_voucher_id and status = 'active'
   returning id into v_id;

  if v_id is null then
    raise exception 'Voucher not found, not in this company, or already cancelled';
  end if;

  return jsonb_build_object('id', v_id, 'status', 'cancelled');
end;
$$;

revoke execute on function public.cancel_voucher(uuid, uuid, uuid, text) from public, anon, authenticated;

commit;
