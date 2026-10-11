-- Migration 014: daily sales totals, for the dashboard's "today's sales"
-- tile and sales-trend graph.
--
-- Run in Supabase Dashboard -> SQL Editor, after 010_accounting_system.sql.

begin;

create view public.v_sales_daily as
select
  v.company_id,
  v.date,
  count(*) as voucher_count,
  sum(v.total_amount) as total_amount
from public.vouchers v
join public.voucher_types vt on vt.company_id = v.company_id and vt.id = v.voucher_type_id
where v.status = 'active' and vt.code = 'sales'
group by v.company_id, v.date;

revoke all on public.v_sales_daily from anon, authenticated;

commit;
