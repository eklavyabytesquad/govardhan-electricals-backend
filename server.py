"""Govardhan Electricals backend — pure Python (standard library only), storage in Supabase.

This file is only the HTTP layer (routing, request/response, CORS). The actual
logic lives in services/*.py — see services/accounting/{masters,vouchers,reports}/README.md
for the full accounting API (financial years, account groups, ledgers, units,
item groups, items, godowns, batches, voucher types, vouchers, reports).

Run:  python server.py
Endpoints (JSON):
  GET  /api/health
  POST /api/register         {name, number, username, password, country_code?="91"}
  POST /api/login            {username | number, password}  -> {token, user}
  GET  /api/me               Authorization: Bearer <token>
  POST /api/logout           Authorization: Bearer <token>
  POST /api/forgot-password  {identifier}                   -> sends OTP to the user's mobile
  POST /api/verify-otp       {identifier, otp}              -> {reset_token}
  POST /api/reset-password   {reset_token, new_password}
  POST /api/contact          {name, phone, message, email?}
  POST /api/companies                {name, gstin?, owner_name?, number?, metadata?}  Bearer token
  GET  /api/companies                —                                   Bearer token
  POST /api/companies/members        {company_id, identifier, role?}     Bearer token
  POST /api/companies/update         {company_id, name?, gstin?, owner_name?, number?, metadata?}  Bearer token, owner/admin only

  GET  /api/notifications                   ?company_id=          Bearer token
  GET  /api/notifications/unread-count      ?company_id=          Bearer token
  POST /api/notifications/read              {company_id, id}      Bearer token
  POST /api/notifications/read-all          {company_id}          Bearer token

  Accounting masters (services/accounting/masters/README.md):
    /api/accounting/financial-years[/close]
    /api/accounting/account-groups[/update]
    /api/accounting/ledgers[/update]
    /api/accounting/units[/update]
    /api/accounting/item-groups[/update]
    /api/accounting/items[/update]
    /api/accounting/godowns[/update]
    /api/accounting/batches[/update]
    /api/accounting/voucher-types[/update]
    /api/accounting/voucher-number-series   GET only

  Accounting vouchers (services/accounting/vouchers/README.md):
    POST /api/accounting/vouchers              {voucher_type_id, financial_year_id, items?, ledger_entries?, bill_allocations?, ...}
    GET  /api/accounting/vouchers?company_id=&voucher_type_id=&status=
    GET  /api/accounting/vouchers/get?company_id=&id=
    POST /api/accounting/vouchers/cancel       {company_id, id, reason?}

  Accounting reports (services/accounting/reports/README.md):
    GET /api/accounting/reports/stock-balance?company_id=&item_id=&godown_id=
    GET /api/accounting/reports/stock-value?company_id=&item_id=
    GET /api/accounting/reports/batch-expiry?company_id=&item_id=
    GET /api/accounting/reports/low-stock?company_id=
    GET /api/accounting/reports/ledger-balance?company_id=&ledger_id=
    GET /api/accounting/reports/bill-outstanding?company_id=&ledger_id=
    GET /api/accounting/reports/sales-profit?company_id=&voucher_id=
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import supabase
from config import ALLOWED_ORIGINS, HOST, PORT
from errors import ApiError
from services import auth_service, company_service, contact_service, notification_service
from services.accounting.masters import (
    account_groups_service, batches_service, financial_years_service, godowns_service,
    item_groups_service, items_service, ledgers_service, units_service,
    voucher_number_series_service, voucher_types_service,
)
from services.accounting.reports import reports_service
from services.accounting.vouchers import voucher_service
from supabase import SupabaseError


def _origin_allowed(origin):
    if not origin:
        return False
    host = urlparse(origin).hostname or ""
    for pattern in ALLOWED_ORIGINS:
        if pattern.startswith("*."):
            root = pattern[2:]
            if host == root or host.endswith("." + root):
                return True
        elif origin == pattern:
            return True
    return False


class Handler(BaseHTTPRequestHandler):
    server_version = "GovardhanAPI/4.0"

    # -- plumbing
    def _cors_headers(self):
        origin = self.headers.get("Origin", "")
        if _origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send(self, status, body=None):
        raw = json.dumps(body if body is not None else {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(raw)

    def _json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 1_000_000:
                raise ApiError(413, "Request too large")
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError
            return data
        except ApiError:
            raise
        except Exception:
            raise ApiError(400, "Invalid JSON body")

    def _bearer(self):
        auth = self.headers.get("Authorization", "")
        return auth[7:] if auth.startswith("Bearer ") else ""

    def _query(self):
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _dispatch(self, method):
        routes = {
            ("GET", "/api/health"): self.health,
            ("POST", "/api/register"): self.register,
            ("POST", "/api/login"): self.login,
            ("GET", "/api/me"): self.me,
            ("POST", "/api/logout"): self.logout,
            ("POST", "/api/forgot-password"): self.forgot_password,
            ("POST", "/api/verify-otp"): self.verify_otp,
            ("POST", "/api/reset-password"): self.reset_password,
            ("POST", "/api/contact"): self.contact,
            ("GET", "/api/public/invoice"): self.public_invoice,
            ("POST", "/api/companies"): self.create_company,
            ("GET", "/api/companies"): self.list_companies,
            ("POST", "/api/companies/members"): self.add_company_member,
            ("POST", "/api/companies/update"): self.update_company,

            ("GET", "/api/notifications"): self.list_notifications,
            ("GET", "/api/notifications/unread-count"): self.notifications_unread_count,
            ("POST", "/api/notifications/read"): self.mark_notification_read,
            ("POST", "/api/notifications/read-all"): self.mark_all_notifications_read,

            # -- accounting masters
            ("POST", "/api/accounting/financial-years"): self.create_financial_year,
            ("GET", "/api/accounting/financial-years"): self.list_financial_years,
            ("POST", "/api/accounting/financial-years/close"): self.close_financial_year,

            ("POST", "/api/accounting/account-groups"): self.create_account_group,
            ("GET", "/api/accounting/account-groups"): self.list_account_groups,
            ("POST", "/api/accounting/account-groups/update"): self.update_account_group,

            ("POST", "/api/accounting/ledgers"): self.create_ledger,
            ("GET", "/api/accounting/ledgers"): self.list_ledgers,
            ("POST", "/api/accounting/ledgers/update"): self.update_ledger,

            ("POST", "/api/accounting/units"): self.create_accounting_unit,
            ("GET", "/api/accounting/units"): self.list_accounting_units,
            ("POST", "/api/accounting/units/update"): self.update_accounting_unit,

            ("POST", "/api/accounting/item-groups"): self.create_item_group,
            ("GET", "/api/accounting/item-groups"): self.list_item_groups,
            ("POST", "/api/accounting/item-groups/update"): self.update_item_group,

            ("POST", "/api/accounting/items"): self.create_item,
            ("GET", "/api/accounting/items"): self.list_items,
            ("POST", "/api/accounting/items/update"): self.update_item,

            ("POST", "/api/accounting/godowns"): self.create_godown,
            ("GET", "/api/accounting/godowns"): self.list_godowns,
            ("POST", "/api/accounting/godowns/update"): self.update_godown,

            ("POST", "/api/accounting/batches"): self.create_batch,
            ("GET", "/api/accounting/batches"): self.list_batches,
            ("POST", "/api/accounting/batches/update"): self.update_batch,

            ("GET", "/api/accounting/voucher-types"): self.list_voucher_types,
            ("POST", "/api/accounting/voucher-types/update"): self.update_voucher_type,

            ("GET", "/api/accounting/voucher-number-series"): self.list_voucher_number_series,

            # -- accounting vouchers
            ("POST", "/api/accounting/vouchers"): self.create_voucher,
            ("GET", "/api/accounting/vouchers"): self.list_vouchers,
            ("GET", "/api/accounting/vouchers/get"): self.get_voucher,
            ("POST", "/api/accounting/vouchers/cancel"): self.cancel_voucher,

            # -- accounting reports
            ("GET", "/api/accounting/reports/stock-balance"): self.report_stock_balance,
            ("GET", "/api/accounting/reports/stock-value"): self.report_stock_value,
            ("GET", "/api/accounting/reports/batch-expiry"): self.report_batch_expiry,
            ("GET", "/api/accounting/reports/low-stock"): self.report_low_stock,
            ("GET", "/api/accounting/reports/ledger-balance"): self.report_ledger_balance,
            ("GET", "/api/accounting/reports/bill-outstanding"): self.report_bill_outstanding,
            ("GET", "/api/accounting/reports/sales-profit"): self.report_sales_profit,
            ("GET", "/api/accounting/reports/ledger-transactions"): self.report_ledger_transactions,
            ("GET", "/api/accounting/reports/sales-daily"): self.report_sales_daily,
        }
        try:
            handler = routes.get((method, self.path.split("?")[0].rstrip("/")))
            if not handler:
                raise ApiError(404, "Not found")
            status, body = handler()
            self._send(status, body)
        except ApiError as e:
            self._send(e.status, {"error": e.message})
        except SupabaseError as e:
            print("Supabase error:", e.status, e.code, e.message)
            self._send(409 if e.code == "23505" else 502,
                       {"error": "Duplicate value" if e.code == "23505" else e.message})
        except Exception as e:  # noqa: BLE001
            print("Server error:", repr(e))
            self._send(500, {"error": "Internal server error"})

    def do_OPTIONS(self):
        self._send(204)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    # -- auth / companies (unchanged)
    def health(self):
        return 200, {"status": "ok"}

    def register(self):
        d = self._json()
        token, user = auth_service.register(
            d.get("name"), d.get("username"), d.get("number"), d.get("country_code"),
            d.get("password"), self.client_address[0], self.headers.get("User-Agent"),
        )
        return 201, {"token": token, "user": user}

    def login(self):
        d = self._json()
        token, user = auth_service.login(
            d.get("username") or d.get("number"), d.get("password"),
            self.client_address[0], self.headers.get("User-Agent"),
        )
        return 200, {"token": token, "user": user}

    def me(self):
        user, _ = auth_service.authenticate(self._bearer())
        return 200, {"user": user}

    def logout(self):
        _, token = auth_service.authenticate(self._bearer())
        auth_service.logout(token)
        return 200, {"ok": True}

    def forgot_password(self):
        auth_service.forgot_password(self._json().get("identifier", ""))
        # Always the same generic message — don't reveal whether an account exists.
        return 200, {"ok": True, "message": "If the account exists, a verification code has been sent."}

    def verify_otp(self):
        d = self._json()
        reset_token = auth_service.verify_otp(d.get("identifier", ""), d.get("otp", ""))
        return 200, {"reset_token": reset_token}

    def reset_password(self):
        d = self._json()
        auth_service.reset_password(d.get("reset_token", ""), d.get("new_password", ""))
        return 200, {"ok": True}

    def contact(self):
        d = self._json()
        contact_service.submit(d.get("name"), d.get("phone"), d.get("email"), d.get("message"))
        return 201, {"ok": True}

    def public_invoice(self):
        # No auth on purpose — this is what the QR code on a printed sales
        # bill opens. See voucher_service.get_public_sale for the scope limits.
        q = self._query()
        return 200, voucher_service.get_public_sale(q.get("id", ""))

    def create_company(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        company = company_service.create(
            user["id"], d.get("name"), d.get("gstin"), d.get("owner_name"), d.get("number"), d.get("metadata"),
        )
        return 201, {"company": company}

    def list_companies(self):
        user, _ = auth_service.authenticate(self._bearer())
        return 200, {"companies": company_service.list_for_user(user["id"])}

    def update_company(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        company = company_service.update(
            user["id"], d.get("company_id"), d.get("name"), d.get("gstin"), d.get("owner_name"),
            d.get("number"), d.get("metadata"),
        )
        return 200, {"company": company}

    def add_company_member(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        result = company_service.add_member(user["id"], d.get("company_id"), d.get("identifier"), d.get("role", "member"))
        return 201, result

    # -- notifications
    def list_notifications(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        rows = notification_service.list_for_user(user["id"], q.get("company_id", ""))
        return 200, {"notifications": rows}

    def notifications_unread_count(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        count = notification_service.unread_count(user["id"], q.get("company_id", ""))
        return 200, {"count": count}

    def mark_notification_read(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        row = notification_service.mark_read(user["id"], d.get("company_id"), d.get("id"))
        return 200, {"notification": row}

    def mark_all_notifications_read(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        result = notification_service.mark_all_read(user["id"], d.get("company_id"))
        return 200, result

    # -- accounting masters: financial years
    def create_financial_year(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        fy = financial_years_service.create(user["id"], d.get("company_id"), d.get("name"), d.get("start_date"), d.get("end_date"))
        return 201, {"financial_year": fy}

    def list_financial_years(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"financial_years": financial_years_service.list_for_company(user["id"], company_id)}

    def close_financial_year(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        fy = financial_years_service.close(user["id"], d.get("company_id"), d.get("id"))
        return 200, {"financial_year": fy}

    # -- accounting masters: account groups
    def create_account_group(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        group = account_groups_service.create(
            user["id"], d.get("company_id"), d.get("name"), d.get("parent_id"),
            d.get("nature"), d.get("affects_gross_profit", False),
        )
        return 201, {"account_group": group}

    def list_account_groups(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"account_groups": account_groups_service.list_for_company(user["id"], company_id)}

    def update_account_group(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        group = account_groups_service.update(user["id"], d.get("company_id"), d.get("id"), d.get("name"), d.get("parent_id"))
        return 200, {"account_group": group}

    # -- accounting masters: ledgers
    def create_ledger(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        ledger = ledgers_service.create(user["id"], d.get("company_id"), d)
        return 201, {"ledger": ledger}

    def list_ledgers(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"ledgers": ledgers_service.list_for_company(user["id"], company_id)}

    def update_ledger(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        ledger = ledgers_service.update(user["id"], d.get("company_id"), d.get("id"), d)
        return 200, {"ledger": ledger}

    # -- accounting masters: units
    def create_accounting_unit(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        unit = units_service.create(user["id"], d.get("company_id"), d.get("name"), d.get("decimal_places", 0))
        return 201, {"unit": unit}

    def list_accounting_units(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"units": units_service.list_for_company(user["id"], company_id)}

    def update_accounting_unit(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        unit = units_service.update(user["id"], d.get("company_id"), d.get("id"), d.get("name"), d.get("decimal_places"))
        return 200, {"unit": unit}

    # -- accounting masters: item groups
    def create_item_group(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        group = item_groups_service.create(user["id"], d.get("company_id"), d.get("name"), d.get("parent_id"))
        return 201, {"item_group": group}

    def list_item_groups(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"item_groups": item_groups_service.list_for_company(user["id"], company_id)}

    def update_item_group(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        group = item_groups_service.update(user["id"], d.get("company_id"), d.get("id"), d.get("name"), d.get("parent_id"))
        return 200, {"item_group": group}

    # -- accounting masters: items
    def create_item(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        item = items_service.create(user["id"], d.get("company_id"), d)
        return 201, {"item": item}

    def list_items(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"items": items_service.list_for_company(user["id"], company_id)}

    def update_item(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        item = items_service.update(user["id"], d.get("company_id"), d.get("id"), d)
        return 200, {"item": item}

    # -- accounting masters: godowns
    def create_godown(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        godown = godowns_service.create(user["id"], d.get("company_id"), d.get("name"), d.get("address"), d.get("is_default", False))
        return 201, {"godown": godown}

    def list_godowns(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"godowns": godowns_service.list_for_company(user["id"], company_id)}

    def update_godown(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        godown = godowns_service.update(
            user["id"], d.get("company_id"), d.get("id"), d.get("name"), d.get("address"), d.get("is_default"),
        )
        return 200, {"godown": godown}

    # -- accounting masters: batches
    def create_batch(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        batch = batches_service.create(
            user["id"], d.get("company_id"), d.get("item_id"), d.get("batch_no"), d.get("mfg_date"), d.get("expiry_date"),
        )
        return 201, {"batch": batch}

    def list_batches(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        return 200, {"batches": batches_service.list_for_company(user["id"], q.get("company_id", ""), q.get("item_id"))}

    def update_batch(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        batch = batches_service.update(user["id"], d.get("company_id"), d.get("id"), d.get("mfg_date"), d.get("expiry_date"))
        return 200, {"batch": batch}

    # -- accounting masters: voucher types + number series
    def list_voucher_types(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"voucher_types": voucher_types_service.list_for_company(user["id"], company_id)}

    def update_voucher_type(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        vt = voucher_types_service.update_prefix(user["id"], d.get("company_id"), d.get("id"), d.get("prefix"))
        return 200, {"voucher_type": vt}

    def list_voucher_number_series(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        series = voucher_number_series_service.list_for_company(user["id"], q.get("company_id", ""), q.get("financial_year_id"))
        return 200, {"voucher_number_series": series}

    # -- accounting vouchers
    def create_voucher(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        voucher = voucher_service.create(user["id"], d.get("company_id"), d)
        return 201, {"voucher": voucher}

    def list_vouchers(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        vouchers = voucher_service.list_for_company(user["id"], q.get("company_id", ""), q.get("voucher_type_id"), q.get("status"))
        return 200, {"vouchers": vouchers}

    def get_voucher(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        voucher = voucher_service.get_one(user["id"], q.get("company_id", ""), q.get("id", ""))
        return 200, {"voucher": voucher}

    def cancel_voucher(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        voucher = voucher_service.cancel(user["id"], d.get("company_id"), d.get("id"), d.get("reason"))
        return 200, {"voucher": voucher}

    # -- accounting reports
    def report_stock_balance(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        rows = reports_service.stock_balance(user["id"], q.get("company_id", ""), q.get("item_id"), q.get("godown_id"))
        return 200, {"stock_balance": rows}

    def report_stock_value(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        rows = reports_service.stock_value(user["id"], q.get("company_id", ""), q.get("item_id"))
        return 200, {"stock_value": rows}

    def report_batch_expiry(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        rows = reports_service.batch_expiry(user["id"], q.get("company_id", ""), q.get("item_id"))
        return 200, {"batch_expiry": rows}

    def report_low_stock(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"low_stock": reports_service.low_stock(user["id"], company_id)}

    def report_ledger_balance(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        rows = reports_service.ledger_balance(user["id"], q.get("company_id", ""), q.get("ledger_id"))
        return 200, {"ledger_balance": rows}

    def report_bill_outstanding(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        rows = reports_service.bill_outstanding(user["id"], q.get("company_id", ""), q.get("ledger_id"))
        return 200, {"bill_outstanding": rows}

    def report_sales_profit(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        rows = reports_service.sales_profit(user["id"], q.get("company_id", ""), q.get("voucher_id"))
        return 200, {"sales_profit": rows}

    def report_ledger_transactions(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        rows = reports_service.ledger_transactions(user["id"], q.get("company_id", ""), q.get("ledger_id", ""))
        return 200, {"ledger_transactions": rows}

    def report_sales_daily(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        rows = reports_service.sales_daily(user["id"], q.get("company_id", ""), q.get("days", 14))
        return 200, {"sales_daily": rows}


if __name__ == "__main__":
    print(supabase.check()[1])
    print(f"Govardhan Electricals API running on http://{HOST}:{PORT}")
    print(f"Allowed origins: {', '.join(ALLOWED_ORIGINS)}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
