"""Govardhan Electricals backend — pure Python (standard library only), storage in Supabase.

This file is only the HTTP layer (routing, request/response, CORS). The actual
logic lives in services/auth_service.py and services/contact_service.py.

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

  POST /api/inventory                {company_id, name, sku?, hsn_code?, unit?, price?, tax_rate?, image_url?, ...}  Bearer token
  GET  /api/inventory?company_id=    —                                                                    Bearer token
  POST /api/inventory/update         {id, company_id, name, ...}                                          Bearer token
  POST /api/inventory/delete         {id, company_id}                                                     Bearer token

  POST /api/series                   {company_id, name, prefix?, suffix?, padding?, is_default?}  Bearer token
  GET  /api/series?company_id=       —                                                             Bearer token

  GET  /api/invoice-config?company_id=   —                                              Bearer token
  POST /api/invoice-config               {company_id, gstin?, address_line1?, bank_name?, ...}  Bearer token

  POST /api/invoices                 {company_id, customer_name, items:[...], series_id? | invoice_number?, ...}  Bearer token
  GET  /api/invoices?company_id=&status=   —                                                                       Bearer token
  GET  /api/invoices/get?company_id=&id=   —                                                                       Bearer token
  POST /api/invoices/update          {company_id, id, customer_name, items:[...], ...}                            Bearer token — full edit, number/status unchanged
  POST /api/invoices/status          {company_id, id, status, amount_paid?}                                       Bearer token
  GET  /api/invoices/search?company_id=&q=&date_from=&date_to=&min_amount=&max_amount=                            Bearer token

  POST /api/customers                {company_id, name, number?, email?, gstin?, address?}  Bearer token
  GET  /api/customers?company_id=    —                                                       Bearer token
  POST /api/customers/update         {id, company_id, name, ...}                             Bearer token
  POST /api/customers/delete         {id, company_id}                                         Bearer token

  POST /api/units                    {company_id, name, abbreviation?}  Bearer token
  GET  /api/units?company_id=        —                                  Bearer token
  POST /api/units/delete             {id, company_id}                   Bearer token
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import supabase
from config import ALLOWED_ORIGINS, HOST, PORT
from errors import ApiError
from services import (
    auth_service, company_service, contact_service, customer_service,
    invoice_config_service, invoice_service, inventory_service, series_service, units_service,
)
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
    server_version = "GovardhanAPI/3.0"

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
            ("POST", "/api/companies"): self.create_company,
            ("GET", "/api/companies"): self.list_companies,
            ("POST", "/api/companies/members"): self.add_company_member,
            ("POST", "/api/companies/update"): self.update_company,
            ("POST", "/api/inventory"): self.create_inventory,
            ("GET", "/api/inventory"): self.list_inventory,
            ("POST", "/api/inventory/update"): self.update_inventory,
            ("POST", "/api/inventory/delete"): self.delete_inventory,
            ("POST", "/api/series"): self.create_series,
            ("GET", "/api/series"): self.list_series,
            ("GET", "/api/invoice-config"): self.get_invoice_config,
            ("POST", "/api/invoice-config"): self.upsert_invoice_config,
            ("POST", "/api/invoices"): self.create_invoice,
            ("GET", "/api/invoices"): self.list_invoices,
            ("GET", "/api/invoices/get"): self.get_invoice,
            ("POST", "/api/invoices/status"): self.update_invoice_status,
            ("POST", "/api/invoices/update"): self.update_invoice,
            ("GET", "/api/invoices/search"): self.search_invoices,
            ("POST", "/api/customers"): self.create_customer,
            ("GET", "/api/customers"): self.list_customers,
            ("POST", "/api/customers/update"): self.update_customer,
            ("POST", "/api/customers/delete"): self.delete_customer,
            ("POST", "/api/units"): self.create_unit,
            ("GET", "/api/units"): self.list_units,
            ("POST", "/api/units/delete"): self.delete_unit,
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

    # -- endpoints (thin: parse request -> call service -> shape response)
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

    # -- inventory
    def create_inventory(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        item = inventory_service.create(
            user["id"], d.get("company_id"),
            name=d.get("name"), sku=d.get("sku"), description=d.get("description"),
            hsn_code=d.get("hsn_code"), unit=d.get("unit"), price=d.get("price"),
            tax_rate=d.get("tax_rate"), stock_quantity=d.get("stock_quantity"),
            reorder_level=d.get("reorder_level"), category=d.get("category"), metadata=d.get("metadata"),
            image_url=d.get("image_url"),
        )
        return 201, {"item": item}

    def list_inventory(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"items": inventory_service.list_for_company(user["id"], company_id)}

    def update_inventory(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        item = inventory_service.update(
            user["id"], d.get("company_id"), d.get("id"),
            name=d.get("name"), sku=d.get("sku"), description=d.get("description"),
            hsn_code=d.get("hsn_code"), unit=d.get("unit"), price=d.get("price"),
            tax_rate=d.get("tax_rate"), stock_quantity=d.get("stock_quantity"),
            reorder_level=d.get("reorder_level"), category=d.get("category"), metadata=d.get("metadata"),
            image_url=d.get("image_url"),
        )
        return 200, {"item": item}

    def delete_inventory(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        inventory_service.delete(user["id"], d.get("company_id"), d.get("id"))
        return 200, {"ok": True}

    # -- invoice numbering series
    def create_series(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        series = series_service.create(
            user["id"], d.get("company_id"), d.get("name"),
            d.get("prefix", ""), d.get("suffix", ""), d.get("padding", 4), d.get("is_default", False),
        )
        return 201, {"series": series}

    def list_series(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"series": series_service.list_for_company(user["id"], company_id)}

    # -- invoice config (template, legal, GSTIN, address, bank details)
    def get_invoice_config(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"config": invoice_config_service.get(user["id"], company_id)}

    def upsert_invoice_config(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        config = invoice_config_service.upsert(user["id"], d.get("company_id"), d)
        return 200, {"config": config}

    # -- invoices
    def create_invoice(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        invoice = invoice_service.create(user["id"], d.get("company_id"), d)
        return 201, {"invoice": invoice}

    def list_invoices(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        invoices = invoice_service.list_for_company(user["id"], q.get("company_id", ""), q.get("status"))
        return 200, {"invoices": invoices}

    def get_invoice(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        invoice = invoice_service.get_one(user["id"], q.get("company_id", ""), q.get("id", ""))
        return 200, {"invoice": invoice}

    def update_invoice(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        invoice = invoice_service.update(user["id"], d.get("company_id"), d.get("id"), d)
        return 200, {"invoice": invoice}

    def update_invoice_status(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        invoice = invoice_service.update_status(
            user["id"], d.get("company_id"), d.get("id"), d.get("status"), d.get("amount_paid"),
        )
        return 200, {"invoice": invoice}

    def search_invoices(self):
        user, _ = auth_service.authenticate(self._bearer())
        q = self._query()
        results = invoice_service.search(
            user["id"], q.get("company_id", ""), q.get("q"), q.get("date_from"), q.get("date_to"),
            q.get("min_amount"), q.get("max_amount"),
        )
        return 200, {"invoices": results}

    # -- customers
    def create_customer(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        customer = customer_service.create(
            user["id"], d.get("company_id"),
            name=d.get("name"), number=d.get("number"), email=d.get("email"),
            gstin=d.get("gstin"), address=d.get("address"),
            default_discount_percent=d.get("default_discount_percent"), metadata=d.get("metadata"),
        )
        return 201, {"customer": customer}

    def list_customers(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"customers": customer_service.list_for_company(user["id"], company_id)}

    def update_customer(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        customer = customer_service.update(
            user["id"], d.get("company_id"), d.get("id"),
            name=d.get("name"), number=d.get("number"), email=d.get("email"),
            gstin=d.get("gstin"), address=d.get("address"),
            default_discount_percent=d.get("default_discount_percent"), metadata=d.get("metadata"),
        )
        return 200, {"customer": customer}

    def delete_customer(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        customer_service.delete(user["id"], d.get("company_id"), d.get("id"))
        return 200, {"ok": True}

    # -- units
    def create_unit(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        unit = units_service.create(user["id"], d.get("company_id"), d.get("name"), d.get("abbreviation"))
        return 201, {"unit": unit}

    def list_units(self):
        user, _ = auth_service.authenticate(self._bearer())
        company_id = self._query().get("company_id", "")
        return 200, {"units": units_service.list_for_company(user["id"], company_id)}

    def delete_unit(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        units_service.delete(user["id"], d.get("company_id"), d.get("id"))
        return 200, {"ok": True}


if __name__ == "__main__":
    print(supabase.check()[1])
    print(f"Govardhan Electricals API running on http://{HOST}:{PORT}")
    print(f"Allowed origins: {', '.join(ALLOWED_ORIGINS)}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
