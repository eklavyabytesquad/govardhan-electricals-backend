# Accounting

Tally-style multi-company accounting on top of `users` / `companies` /
`company_members`. Schema: `migrations/010_accounting_system.sql`. Voucher
save/cancel RPCs: `migrations/011_save_voucher_rpc.sql`.

Every table is scoped by `company_id`; every endpoint below requires a
`Bearer` token and checks the caller is a member (read) or owner/admin
(write) of that company — see `services/company_service.py`.

- **[masters/](masters/README.md)** — the setup data: financial years, account
  groups, ledgers, units, item groups, items, godowns, batches, voucher
  types. Seeded automatically for every company; editable from here.
- **[vouchers/](vouchers/README.md)** — purchases, sales, payments,
  receipts, returns, journals, contra, stock movements. The only way to
  write financial data.
- **[reports/](reports/README.md)** — stock, ledger balances, outstanding
  bills, profit. Read-only; nothing here is stored, it's all computed from
  vouchers on every call.

Nothing here has a delete endpoint — vouchers are cancelled (`status =
'cancelled'`), financial years are closed, everything else is edited in
place. This matches migration 010's "never hard-delete" rule and the
`ON DELETE RESTRICT` foreign keys underneath it.
