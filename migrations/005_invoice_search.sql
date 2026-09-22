-- Migration 005: invoice search.
-- search_invoices() matches on invoice number, customer name, and line-item
-- descriptions (a join PostgREST can't express directly), plus date and
-- amount ranges — always scoped to one company. Callable via RPC:
-- POST /rest/v1/rpc/search_invoices.
--
-- Run in Supabase Dashboard -> SQL Editor.

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
