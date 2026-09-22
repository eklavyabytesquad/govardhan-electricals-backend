"""Govardhan Electricals backend — pure Python (standard library only), storage in Supabase.

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
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import supabase
from otp import send_otp
from supabase import SupabaseError

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "http://localhost:3000")

SESSION_TTL = timedelta(days=7)
RESET_TOKEN_TTL = timedelta(minutes=15)
OTP_TTL = timedelta(minutes=10)
OTP_RESEND_WAIT = timedelta(seconds=60)
OTP_MAX_ATTEMPTS = 5

USERNAME_RE = re.compile(r"^[a-z0-9_.]{3,30}$")
USER_COLS = "id,name,username,number,country_code"


# ---------- helpers ----------
def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat()


def parse(ts):
    return datetime.fromisoformat(ts)


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    salt_hex, _ = stored.split("$")
    return hmac.compare_digest(hash_password(password, bytes.fromhex(salt_hex)), stored)


def find_user(identifier, cols=USER_COLS + ",password_hash"):
    """Look a user up by username, or by mobile number if the identifier is all digits."""
    ident = str(identifier).strip().lower()
    if not ident:
        return None
    rows = supabase.select("users", {"username": f"eq.{ident}"}, cols, 1)
    if not rows and ident.isdigit():
        rows = supabase.select("users", {"number": f"eq.{ident}"}, cols, 1)
    return rows[0] if rows else None


def public_user(u):
    return {k: u[k] for k in ("id", "name", "username", "number", "country_code")}


def revoke_user_access(user_id):
    """Log the user out everywhere (used after a password reset)."""
    stamp = {"revoked_at": iso(now())}
    supabase.update("tokens", {"user_id": f"eq.{user_id}", "revoked_at": "is.null"}, stamp)
    supabase.update("sessions", {"user_id": f"eq.{user_id}", "revoked_at": "is.null"}, stamp)


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Handler(BaseHTTPRequestHandler):
    server_version = "GovardhanAPI/2.0"

    # -- plumbing
    def _send(self, status, body=None):
        raw = json.dumps(body if body is not None else {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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

    def _authenticate(self):
        raw = self._bearer()
        rows = supabase.select(
            "tokens",
            {"token_hash": f"eq.{sha256(raw)}", "type": "eq.access", "revoked_at": "is.null"},
            "*", 1,
        ) if raw else []
        if not rows or parse(rows[0]["expires_at"]) <= now():
            raise ApiError(401, "Not authenticated")
        users = supabase.select("users", {"id": f"eq.{rows[0]['user_id']}"}, USER_COLS, 1)
        if not users:
            raise ApiError(401, "Not authenticated")
        return users[0], rows[0]

    def _new_session(self, user_id):
        sid, raw, expires = str(uuid.uuid4()), secrets.token_urlsafe(32), now() + SESSION_TTL
        supabase.insert("sessions", {
            "id": sid, "user_id": user_id, "expires_at": iso(expires),
            "ip_address": self.client_address[0],
            "user_agent": self.headers.get("User-Agent", "")[:300],
        })
        supabase.insert("tokens", {
            "user_id": user_id, "session_id": sid, "token_hash": sha256(raw),
            "type": "access", "expires_at": iso(expires),
        })
        return raw

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
            self._send(409 if e.code == "23505" else 502, {"error": "Duplicate value" if e.code == "23505" else e.message})
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

    # -- endpoints
    def health(self):
        return 200, {"status": "ok"}

    def register(self):
        d = self._json()
        name = str(d.get("name", "")).strip()
        username = str(d.get("username", "")).strip().lower()
        number = re.sub(r"[\s-]", "", str(d.get("number", "")))
        country_code = str(d.get("country_code") or "91").lstrip("+").strip()
        password = str(d.get("password", ""))
        if not name:
            raise ApiError(400, "Name is required")
        if not USERNAME_RE.match(username):
            raise ApiError(400, "Username must be 3-30 characters: letters, numbers, _ or .")
        if not (number.isdigit() and 6 <= len(number) <= 15) or not (country_code.isdigit() and 1 <= len(country_code) <= 4):
            raise ApiError(400, "Enter a valid mobile number and country code")
        if len(password) < 6:
            raise ApiError(400, "Password must be at least 6 characters")
        if supabase.select("users", {"username": f"eq.{username}"}, "id", 1):
            raise ApiError(409, "Username is already taken")
        if supabase.select("users", {"number": f"eq.{number}", "country_code": f"eq.{country_code}"}, "id", 1):
            raise ApiError(409, "An account with this mobile number already exists")
        user = supabase.insert("users", {
            "name": name, "username": username, "number": number,
            "country_code": country_code, "password_hash": hash_password(password),
        })
        return 201, {"token": self._new_session(user["id"]), "user": public_user(user)}

    def login(self):
        d = self._json()
        user = find_user(d.get("username") or d.get("number") or "")
        password = str(d.get("password", ""))
        if not user:
            hash_password(password)  # keep timing similar for unknown users
            raise ApiError(401, "Invalid username or password")
        if not verify_password(password, user["password_hash"]):
            raise ApiError(401, "Invalid username or password")
        return 200, {"token": self._new_session(user["id"]), "user": public_user(user)}

    def me(self):
        user, _ = self._authenticate()
        return 200, {"user": user}

    def logout(self):
        _, token = self._authenticate()
        stamp = {"revoked_at": iso(now())}
        supabase.update("tokens", {"id": f"eq.{token['id']}"}, stamp)
        if token.get("session_id"):
            supabase.update("sessions", {"id": f"eq.{token['session_id']}"}, stamp)
        return 200, {"ok": True}

    def forgot_password(self):
        user = find_user(self._json().get("identifier", ""))
        generic = (200, {"ok": True, "message": "If the account exists, a verification code has been sent."})
        if not user:
            return generic
        recent = supabase.select(
            "otp_records", {"user_id": f"eq.{user['id']}", "purpose": "eq.password_reset"},
            "created_at", 1, "created_at.desc",
        )
        if recent and now() - parse(recent[0]["created_at"]) < OTP_RESEND_WAIT:
            raise ApiError(429, "Please wait a minute before requesting another code")
        # only one live code at a time
        supabase.update(
            "otp_records",
            {"user_id": f"eq.{user['id']}", "purpose": "eq.password_reset", "consumed_at": "is.null"},
            {"consumed_at": iso(now())},
        )
        code, rec_id = f"{secrets.randbelow(1_000_000):06d}", str(uuid.uuid4())
        supabase.insert("otp_records", {
            "id": rec_id, "user_id": user["id"], "purpose": "password_reset",
            "otp_hash": sha256(f"{rec_id}:{code}"), "max_attempts": OTP_MAX_ATTEMPTS,
            "expires_at": iso(now() + OTP_TTL),
        })
        try:
            send_otp(user["country_code"], user["number"], code)
        except RuntimeError as e:
            print("OTP send failed:", e)
            supabase.update("otp_records", {"id": f"eq.{rec_id}"}, {"consumed_at": iso(now())})
            raise ApiError(502, "Could not send the verification code. Please try again.")
        return generic

    def verify_otp(self):
        d = self._json()
        bad = ApiError(400, "Invalid or expired code")
        user = find_user(d.get("identifier", ""))
        code = str(d.get("otp", "")).strip()
        if not user or not code:
            raise bad
        recs = supabase.select(
            "otp_records",
            {"user_id": f"eq.{user['id']}", "purpose": "eq.password_reset", "consumed_at": "is.null"},
            "*", 1, "created_at.desc",
        )
        if not recs or parse(recs[0]["expires_at"]) <= now():
            raise bad
        rec = recs[0]
        if rec["attempts"] >= rec["max_attempts"]:
            raise ApiError(429, "Too many wrong attempts. Request a new code.")
        if not hmac.compare_digest(rec["otp_hash"], sha256(f"{rec['id']}:{code}")):
            supabase.update("otp_records", {"id": f"eq.{rec['id']}"}, {"attempts": rec["attempts"] + 1})
            raise bad
        supabase.update("otp_records", {"id": f"eq.{rec['id']}"}, {"consumed_at": iso(now())})
        raw = secrets.token_urlsafe(32)
        supabase.insert("tokens", {
            "user_id": user["id"], "token_hash": sha256(raw), "type": "password_reset",
            "expires_at": iso(now() + RESET_TOKEN_TTL),
        })
        return 200, {"reset_token": raw}

    def reset_password(self):
        d = self._json()
        raw, new_password = str(d.get("reset_token", "")), str(d.get("new_password", ""))
        if len(new_password) < 6:
            raise ApiError(400, "Password must be at least 6 characters")
        rows = supabase.select(
            "tokens",
            {"token_hash": f"eq.{sha256(raw)}", "type": "eq.password_reset", "revoked_at": "is.null"},
            "*", 1,
        ) if raw else []
        if not rows or parse(rows[0]["expires_at"]) <= now():
            raise ApiError(400, "Reset link expired. Please start again.")
        user_id = rows[0]["user_id"]
        supabase.update("users", {"id": f"eq.{user_id}"},
                        {"password_hash": hash_password(new_password), "updated_at": iso(now())})
        supabase.update("tokens", {"id": f"eq.{rows[0]['id']}"}, {"revoked_at": iso(now())})
        revoke_user_access(user_id)
        return 200, {"ok": True}

    def contact(self):
        d = self._json()
        name, phone = str(d.get("name", "")).strip(), str(d.get("phone", "")).strip()
        email, message = str(d.get("email", "")).strip(), str(d.get("message", "")).strip()
        if not name or not phone or not message:
            raise ApiError(400, "Name, phone and message are required")
        supabase.insert("enquiries", {"name": name, "phone": phone, "email": email or None, "message": message})
        return 201, {"ok": True}


if __name__ == "__main__":
    print(supabase.check()[1])
    print(f"Govardhan Electricals API running on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
