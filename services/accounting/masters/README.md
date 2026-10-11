# Accounting masters

Setup data — created once, changed rarely. Every company gets a full set of
these automatically when it's created (`seed_company_defaults()` trigger,
`migrations/010_accounting_system.sql`). All endpoints need `Authorization:
Bearer <token>`. `GET`/list needs any membership; `POST` (create/update)
needs `owner` or `admin`.

| File | Table | Endpoints |
|---|---|---|
| `financial_years_service.py` | `financial_years` | `POST /api/accounting/financial-years`, `GET /api/accounting/financial-years?company_id=`, `POST /api/accounting/financial-years/close` |
| `account_groups_service.py` | `account_groups` | `POST /api/accounting/account-groups`, `GET .../account-groups?company_id=`, `POST .../account-groups/update` |
| `ledgers_service.py` | `ledgers` | `POST /api/accounting/ledgers`, `GET .../ledgers?company_id=`, `POST .../ledgers/update` |
| `units_service.py` | `units` | `POST /api/accounting/units`, `GET .../units?company_id=`, `POST .../units/update` |
| `item_groups_service.py` | `item_groups` | `POST /api/accounting/item-groups`, `GET .../item-groups?company_id=`, `POST .../item-groups/update` |
| `items_service.py` | `items` | `POST /api/accounting/items`, `GET .../items?company_id=`, `POST .../items/update` |
| `godowns_service.py` | `godowns` | `POST /api/accounting/godowns`, `GET .../godowns?company_id=`, `POST .../godowns/update` |
| `batches_service.py` | `batches` | `POST /api/accounting/batches`, `GET .../batches?company_id=&item_id=`, `POST .../batches/update` |
| `voucher_types_service.py` | `voucher_types` | `GET /api/accounting/voucher-types?company_id=`, `POST .../voucher-types/update` (prefix only) |
| `voucher_number_series_service.py` | `voucher_number_series` | `GET /api/accounting/voucher-number-series?company_id=&financial_year_id=` (read-only) |

## Notes

- **No delete endpoints.** Every composite foreign key underneath these
  tables is `ON DELETE RESTRICT`, so deleting master data that's actually in
  use would just fail anyway. Deactivate instead (`is_active` on `items` /
  `ledgers`) or leave it unused.
- **`account_groups`**: rows seeded as `is_system = true` (the standard
  Assets/Liabilities/Income/Expense tree) can't be renamed or re-parented —
  `update()` returns `403` for those. Only groups you create yourself are
  editable.
- **`ledgers`**: rows with a `system_role` (Cash, Sales A/c, Purchase A/c,
  the six GST ledgers, Round Off, Discount) are seeded automatically.
  `system_role` itself is never a settable field through this API — only
  the app's own triggers set it, at seed time.
- **`voucher_types`**: `name` / `code` / `stock_effect` / `affects_accounts`
  are structural and fixed at seed time (a `CHECK` constraint limits `code`
  to the 15 known types). Only `prefix` (the numbering prefix, e.g.
  `INV/24-25/`) is editable.
- **`voucher_number_series`**: has no write endpoint at all. Rows are
  created automatically — once per voucher type when the company is
  created, and again for every voucher type whenever
  `financial_years_service.create()` opens a new year. The actual number is
  only ever claimed atomically inside `save_voucher()` (see `vouchers/`),
  never set directly.
- **`godowns`**: only one `is_default = true` row per company is allowed (a
  partial unique index enforces it at the DB level). `create()`/`update()`
  clear any existing default first so you never hit that constraint by
  surprise.
- **`financial_years.create()`** also inserts a `voucher_number_series` row
  for every voucher type in the company for that year — without it, nothing
  could auto-number in the new year.
