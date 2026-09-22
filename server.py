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
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import supabase
from config import ALLOWED_ORIGINS, HOST, PORT
from errors import ApiError
from services import auth_service, company_service, contact_service
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

    def add_company_member(self):
        user, _ = auth_service.authenticate(self._bearer())
        d = self._json()
        result = company_service.add_member(user["id"], d.get("company_id"), d.get("identifier"), d.get("role", "member"))
        return 201, result


if __name__ == "__main__":
    print(supabase.check()[1])
    print(f"Govardhan Electricals API running on http://{HOST}:{PORT}")
    print(f"Allowed origins: {', '.join(ALLOWED_ORIGINS)}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
