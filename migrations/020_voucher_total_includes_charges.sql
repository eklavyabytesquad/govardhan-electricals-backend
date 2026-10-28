-- Migration 020: fix total_amount for stock vouchers (sale/purchase/
-- credit note/debit note) that carry additional charges.
--
-- save_voucher() (011) and update_voucher() (017) set total_amount to the
-- sum of item line_totals only. That was fine until the frontend started
-- letting a line add extra ledger_entries for things like Transport/
-- Loading/Unloading charges or a Round Off, on top of the items — those
-- entries were correctly reflected in the party's own debit/credit and in
-- the bill_allocations amount (so the ledger and bill-outstanding reports
-- were always right), but total_amount itself — the figure the voucher
-- list, the printed bill, and the public invoice page show — silently
-- stayed short by whatever the charges added.
--
-- Fix: the frontend already computes the one true "full invoice amount"
-- (items + tax + charges) and puts it in the bill_allocations row it sends
-- for every stock voucher, regardless of ref_type ('new'/'against'/
-- 'on_account'). Summing that instead of just the item lines gives the
-- correct total without the RPC needing to know what a "charge" is.
-- Falls back to the old item-only sum if no bill_allocations were sent
-- (shouldn't happen for a stock voucher, but cheap to keep safe).
--
-- Run in Supabase Dashboard -> SQL Editor, after 017_update_voucher_rpc.sql.

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
  v_alloc_total         numeric(14,2) := 0;
  v_item_count         int := 0;
  v_voucher_id         uuid;
  v_item               jsonb;
  v_entry              jsonb;
  v_alloc              jsonb;
  v_line_no            int := 0;
  v_final_total        numeric(14,2);
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
    v_alloc_total := v_alloc_total + coalesce((v_alloc->>'amount')::numeric, 0);
  end loop;

  -- total_amount is a display figure: for stock vouchers, the full invoice
  -- amount (items + tax + any additional charges) — the bill_allocations
  -- total the caller already computed, falling back to item lines only if
  -- none were sent. Pure-money vouchers (payment, receipt, journal...)
  -- that have no items at all use the sum of debits instead.
  v_final_total := case when v_item_count > 0 then coalesce(nullif(v_alloc_total, 0), v_total) else v_debit_total end;
  update public.vouchers set total_amount = v_final_total where id = v_voucher_id;

  -- Force the deferred balance trigger to run NOW, inside this same
  -- transaction, so an unbalanced voucher fails loudly right here with a
  -- clear error — instead of only surfacing at the implicit commit after
  -- this function returns.
  set constraints trg_ledger_entries_balanced immediate;

  return jsonb_build_object('id', v_voucher_id, 'voucher_no', v_voucher_no, 'total_amount', v_final_total);
end;
$$;

revoke execute on function public.save_voucher(jsonb) from public, anon, authenticated;

create or replace function public.update_voucher(p_payload jsonb)
returns jsonb
language plpgsql
as $$
declare
  v_company_id      uuid := (p_payload->>'company_id')::uuid;
  v_voucher_id      uuid := (p_payload->>'id')::uuid;
  v_user_id         uuid := (p_payload->>'updated_by')::uuid;
  v_date            date := coalesce(nullif(p_payload->>'date', '')::date, current_date);
  v_party_ledger_id uuid := nullif(p_payload->>'party_ledger_id', '')::uuid;
  v_status          text;
  v_voucher_no      text;
  v_total           numeric(14,2) := 0;
  v_debit_total     numeric(14,2) := 0;
  v_alloc_total      numeric(14,2) := 0;
  v_item_count      int := 0;
  v_item            jsonb;
  v_entry           jsonb;
  v_alloc           jsonb;
  v_line_no         int := 0;
  v_final_total     numeric(14,2);
begin
  if v_company_id is null or v_voucher_id is null or v_user_id is null then
    raise exception 'company_id, id and updated_by are all required';
  end if;

  -- Lock the row so two concurrent edits of the same voucher can't interleave.
  select status, voucher_no into v_status, v_voucher_no
    from public.vouchers
   where company_id = v_company_id and id = v_voucher_id
   for update;

  if v_voucher_no is null then
    raise exception 'Voucher not found in this company';
  end if;
  if v_status <> 'active' then
    raise exception 'Only an active voucher can be edited (this one is %)', v_status;
  end if;

  update public.vouchers
     set date = v_date,
         party_ledger_id = v_party_ledger_id,
         reference_no = p_payload->>'reference_no',
         reference_date = nullif(p_payload->>'reference_date', '')::date,
         place_of_supply = p_payload->>'place_of_supply',
         narration = p_payload->>'narration',
         updated_by = v_user_id,
         updated_at = now()
   where id = v_voucher_id;

  delete from public.bill_allocations where voucher_id = v_voucher_id;
  delete from public.ledger_entries where voucher_id = v_voucher_id;
  delete from public.voucher_items where voucher_id = v_voucher_id;

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
    v_alloc_total := v_alloc_total + coalesce((v_alloc->>'amount')::numeric, 0);
  end loop;

  v_final_total := case when v_item_count > 0 then coalesce(nullif(v_alloc_total, 0), v_total) else v_debit_total end;
  update public.vouchers set total_amount = v_final_total where id = v_voucher_id;

  -- Same reasoning as save_voucher: force the deferred balance trigger to
  -- run now, inside this transaction, so an unbalanced edit fails loudly
  -- right here instead of at the implicit commit after this function returns.
  set constraints trg_ledger_entries_balanced immediate;

  return jsonb_build_object('id', v_voucher_id, 'voucher_no', v_voucher_no, 'total_amount', v_final_total);
end;
$$;

revoke execute on function public.update_voucher(jsonb) from public, anon, authenticated;

commit;
