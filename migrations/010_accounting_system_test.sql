-- Test script for migration 010_accounting_system.sql.
--
-- IMPORTANT: this SQL has NOT been run against a live database — there was
-- no local Postgres/Docker instance available to test it against before
-- handing it to you. Read every RAISE NOTICE line in the output carefully;
-- each check should print "PASS: ...". Anything else is a real bug in the
-- migration, not a false alarm — stop and report it rather than assuming
-- it's fine.
--
-- Runs entirely inside BEGIN ... ROLLBACK, so nothing it does is kept
-- afterwards — not the two test users, not the two test companies, nothing
-- — whether the tests pass or fail. Run the WHOLE file as one query in
-- Supabase Dashboard -> SQL Editor (don't split it into pieces, or the
-- final ROLLBACK won't be the one undoing your test data).
--
-- Covers the five checks the migration brief asked for:
--   1. Two companies, each gets its own independent default data.
--   2. Purchase 10, sell 3 -> stock view shows 7, ledger balances correct.
--   3. An unbalanced voucher (debit <> credit) is rejected at commit.
--   4. A voucher in company A referencing company B's item is rejected
--      by the composite foreign key.
--   5. Cancelling a voucher removes it from the views (v_stock_balance
--      here; v_ledger_balance / v_bill_outstanding / v_sales_profit use
--      the exact same "join vouchers, filter status = 'active'" pattern).

begin;

do $$
declare
  v_user_a uuid;
  v_user_b uuid;
  v_company_a uuid;
  v_company_b uuid;
  v_unit_a uuid;
  v_unit_b uuid;
  v_godown_a uuid;
  v_item_a uuid;
  v_item_b uuid;
  v_cash_a uuid;
  v_sales_a uuid;
  v_fy_a uuid;
  v_vt_purchase_a uuid;
  v_vt_sales_a uuid;
  v_purchase_voucher uuid;
  v_sale_voucher uuid;
  v_bad_voucher uuid;
  v_stock numeric;
  v_group_count int;
  v_ok boolean;
begin
  raise notice '--- setup: 2 users, 2 companies ---';

  insert into public.users (name, number, username, password_hash)
  values ('Test User A', '9000000001', 'test_user_a_010', 'x')
  returning id into v_user_a;

  insert into public.users (name, number, username, password_hash)
  values ('Test User B', '9000000002', 'test_user_b_010', 'x')
  returning id into v_user_b;

  insert into public.companies (name, created_by) values ('Test Co A', v_user_a) returning id into v_company_a;
  insert into public.companies (name, created_by) values ('Test Co B', v_user_b) returning id into v_company_b;

  -- ---- test 1: each company got its own defaults, independently ----
  select count(*) into v_group_count from public.account_groups where company_id = v_company_a;
  if v_group_count = 0 then raise exception 'FAIL: test 1 — company A got no account_groups'; end if;

  select count(*) into v_group_count from public.account_groups where company_id = v_company_b;
  if v_group_count = 0 then raise exception 'FAIL: test 1 — company B got no account_groups'; end if;

  if (select count(*) from public.ledgers where company_id = v_company_a) = 0 then
    raise exception 'FAIL: test 1 — company A got no ledgers';
  end if;

  if (select count(*) from public.voucher_types where company_id = v_company_a) <> 15 then
    raise exception 'FAIL: test 1 — company A should have exactly 15 voucher types';
  end if;
  raise notice 'PASS: test 1 — companies A and B each got independent default data';

  -- pull what we need from each company's seeded data
  select id into v_unit_a from public.units where company_id = v_company_a and name = 'pcs';
  select id into v_godown_a from public.godowns where company_id = v_company_a and is_default;
  select id into v_cash_a from public.ledgers where company_id = v_company_a and system_role = 'cash';
  select id into v_sales_a from public.ledgers where company_id = v_company_a and system_role = 'sales';
  select id into v_fy_a from public.financial_years where company_id = v_company_a limit 1;
  select id into v_vt_purchase_a from public.voucher_types where company_id = v_company_a and code = 'purchase';
  select id into v_vt_sales_a from public.voucher_types where company_id = v_company_a and code = 'sales';
  select id into v_unit_b from public.units where company_id = v_company_b and name = 'pcs';

  insert into public.items (company_id, name, unit_id, created_by)
  values (v_company_a, 'Widget', v_unit_a, v_user_a) returning id into v_item_a;

  insert into public.items (company_id, name, unit_id, created_by)
  values (v_company_b, 'Gadget', v_unit_b, v_user_b) returning id into v_item_b;

  -- ---- test 2: purchase 10, sell 3 -> stock = 7, ledgers balance ----
  raise notice '--- test 2: purchase 10, sell 3 ---';

  -- purchase: qty_in 10 @ rate 50 = 500. Dr Purchase 500 / Cr Cash 500.
  insert into public.vouchers (company_id, voucher_type_id, financial_year_id, voucher_no, date, created_by)
  values (v_company_a, v_vt_purchase_a, v_fy_a, 'PUR/TEST/0001', current_date, v_user_a)
  returning id into v_purchase_voucher;

  insert into public.voucher_items (company_id, voucher_id, line_no, item_id, godown_id, qty_in, rate, taxable_amount, line_total)
  values (v_company_a, v_purchase_voucher, 1, v_item_a, v_godown_a, 10, 50, 500, 500);

  insert into public.ledger_entries (company_id, voucher_id, ledger_id, debit, credit) values
    (v_company_a, v_purchase_voucher, (select id from public.ledgers where company_id = v_company_a and system_role = 'purchase'), 500, 0),
    (v_company_a, v_purchase_voucher, v_cash_a, 0, 500);

  -- sale: qty_out 3 @ rate 80 = 240, cost_rate 50. Dr Cash 240 / Cr Sales 240.
  insert into public.vouchers (company_id, voucher_type_id, financial_year_id, voucher_no, date, created_by)
  values (v_company_a, v_vt_sales_a, v_fy_a, 'INV/TEST/0001', current_date, v_user_a)
  returning id into v_sale_voucher;

  insert into public.voucher_items (company_id, voucher_id, line_no, item_id, godown_id, qty_out, rate, cost_rate, taxable_amount, line_total)
  values (v_company_a, v_sale_voucher, 1, v_item_a, v_godown_a, 3, 80, 50, 240, 240);

  insert into public.ledger_entries (company_id, voucher_id, ledger_id, debit, credit) values
    (v_company_a, v_sale_voucher, v_cash_a, 240, 0),
    (v_company_a, v_sale_voucher, v_sales_a, 0, 240);

  set constraints all immediate; -- force the deferred balance trigger to run now, not at commit

  select coalesce(sum(balance_qty), 0) into v_stock
    from public.v_stock_balance
   where company_id = v_company_a and item_id = v_item_a;
  if v_stock <> 7 then raise exception 'FAIL: test 2 — expected stock 7, got %', v_stock; end if;

  if (select closing_balance from public.v_ledger_balance where company_id = v_company_a and ledger_id = v_cash_a) <> -260 then
    raise exception 'FAIL: test 2 — cash ledger balance wrong, expected -260 (paid 500 out, received 240)';
  end if;
  raise notice 'PASS: test 2 — stock = 7, ledger balances correct';

  set constraints all deferred; -- restore deferred mode for the rest of this transaction

  -- ---- test 3: unbalanced voucher rejected ----
  raise notice '--- test 3: unbalanced voucher ---';
  v_ok := false;
  begin
    insert into public.vouchers (company_id, voucher_type_id, financial_year_id, voucher_no, date, created_by)
    values (v_company_a, v_vt_purchase_a, v_fy_a, 'PUR/TEST/0002', current_date, v_user_a)
    returning id into v_bad_voucher;

    insert into public.ledger_entries (company_id, voucher_id, ledger_id, debit, credit)
    values (v_company_a, v_bad_voucher, v_cash_a, 100, 0); -- credit side left out on purpose

    set constraints all immediate;
    v_ok := false; -- if we reach here, the trigger failed to catch it
  exception when others then
    v_ok := true;
    raise notice 'correctly rejected: %', sqlerrm;
  end;
  set constraints all deferred;
  if not v_ok then raise exception 'FAIL: test 3 — unbalanced voucher was NOT rejected'; end if;
  raise notice 'PASS: test 3 — unbalanced voucher rejected at commit (forced early here)';

  -- ---- test 4: cross-company reference rejected by composite FK ----
  raise notice '--- test 4: cross-company item reference ---';
  v_ok := false;
  begin
    insert into public.voucher_items (company_id, voucher_id, line_no, item_id, godown_id, qty_in, rate, taxable_amount, line_total)
    values (v_company_a, v_purchase_voucher, 2, v_item_b, v_godown_a, 1, 10, 10, 10); -- v_item_b belongs to company B
    v_ok := false;
  exception when others then
    v_ok := true;
    raise notice 'correctly rejected: %', sqlerrm;
  end;
  if not v_ok then raise exception 'FAIL: test 4 — cross-company item reference was NOT rejected'; end if;
  raise notice 'PASS: test 4 — cross-company reference rejected by the composite FK';

  -- ---- test 5: cancelled vouchers disappear from views ----
  raise notice '--- test 5: cancel the sale ---';
  update public.vouchers set status = 'cancelled' where id = v_sale_voucher;

  select coalesce(sum(balance_qty), 0) into v_stock
    from public.v_stock_balance
   where company_id = v_company_a and item_id = v_item_a;
  if v_stock <> 10 then
    raise exception 'FAIL: test 5 — expected stock back to 10 after cancelling the sale, got %', v_stock;
  end if;
  raise notice 'PASS: test 5 — cancelled voucher excluded from v_stock_balance (v_ledger_balance / v_bill_outstanding / v_sales_profit use the same status = ''active'' filter)';

  raise notice '=== ALL TESTS PASSED ===';
end $$;

rollback;
