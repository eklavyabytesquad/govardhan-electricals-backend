-- Migration 021: voucher_items.gst_inclusive — was this line's rate
-- entered as a GST-inclusive price (e.g. "15 rupees, already includes 18%
-- GST") or the usual GST-exclusive price (tax added on top of the rate)?
--
-- This has to be stored, not just computed on the fly: the resulting
-- taxable_amount/cgst/sgst/igst are the same either way once saved, but
-- re-opening the voucher to edit it needs to know which formula the
-- original rate was entered under, or it'll recompute the wrong taxable
-- value from the same rate number.
--
-- Run in Supabase Dashboard -> SQL Editor, after 020_voucher_total_includes_charges.sql.

begin;

alter table public.voucher_items
  add column if not exists gst_inclusive boolean not null default false;

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
      cgst, sgst, igst, cess, line_total, cost_rate, gst_inclusive
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
      nullif(v_item->>'cost_rate', '')::numeric,
      coalesce((v_item->>'gst_inclusive')::boolean, false)
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
      cgst, sgst, igst, cess, line_total, cost_rate, gst_inclusive
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
      nullif(v_item->>'cost_rate', '')::numeric,
      coalesce((v_item->>'gst_inclusive')::boolean, false)
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

  set constraints trg_ledger_entries_balanced immediate;

  return jsonb_build_object('id', v_voucher_id, 'voucher_no', v_voucher_no, 'total_amount', v_final_total);
end;
$$;

revoke execute on function public.update_voucher(jsonb) from public, anon, authenticated;

commit;
