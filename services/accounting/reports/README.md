# Reports

Read-only. Every function is a `SELECT` against a view from
`migrations/010_accounting_system.sql` — nothing is stored here, stock
levels and balances are computed fresh from `vouchers` / `voucher_items` /
`ledger_entries` on every call. Cancelled vouchers (`status = 'cancelled'`)
are excluded by every view already, so cancelling something removes it from
every report automatically.

| Endpoint | View | Returns |
|---|---|---|
| `GET /api/accounting/reports/stock-balance?company_id=&item_id=&godown_id=` | `v_stock_balance` | Qty on hand per item / godown / batch |
| `GET /api/accounting/reports/stock-value?company_id=&item_id=` | `v_stock_value` | Qty × weighted-average inbound cost, per item |
| `GET /api/accounting/reports/batch-expiry?company_id=&item_id=` | `v_batch_expiry` | Batches with an `expiry_date`, nearest first |
| `GET /api/accounting/reports/low-stock?company_id=` | `v_low_stock` | Items whose stock is below `reorder_level` |
| `GET /api/accounting/reports/ledger-balance?company_id=&ledger_id=` | `v_ledger_balance` | Opening balance ± all active-voucher entries, per ledger |
| `GET /api/accounting/reports/bill-outstanding?company_id=&ledger_id=` | `v_bill_outstanding` | Each `new` bill minus what's been settled `against` it, with `days_overdue` |
| `GET /api/accounting/reports/sales-profit?company_id=&voucher_id=` | `v_sales_profit` | Per sale line: `(rate - cost_rate) × qty - discount` |
| `GET /api/accounting/reports/ledger-transactions?company_id=&ledger_id=` | `v_ledger_transactions` (migration 012) | Every active voucher that touched one ledger, dated and in order — the "ledger account" statement. `ledger_id` is required (400 without it). |

All filters besides `company_id` are optional — omit them to get every row
for the company.

## P&L and Balance Sheet

Not an endpoint here. Per the migration brief, they're built in the
**frontend** from `ledger-balance` grouped by the `account_groups` tree
(fetch both and group client-side), plus opening/closing stock value from
`stock-value`. Doing the grouping server-side would mean re-implementing
the tree walk in Python for no real benefit — the raw balances and the
group tree are both already one call away.
