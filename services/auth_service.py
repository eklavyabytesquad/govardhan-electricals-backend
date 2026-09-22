"""User accounts: register, login, sessions, and OTP-based password reset.

Tables used (see db.sql): users, sessions, tokens, otp_records.
"""
import hmac
import re
import uuid

import supabase
from config import (
    OTP_MAX_ATTEMPTS, OTP_RESEND_WAIT, OTP_TTL, RESET_TOKEN_TTL,
    SESSION_TTL, USER_COLS, USERNAME_RE,
)
from errors import ApiError
from otp import send_otp
from security import hash_password, iso, new_otp, new_token, now, parse, sha256, verify_password


def find_user(identifier, cols=USER_COLS + ",password_hash"):
    """Look a user up by username, or by mobile number if the identifier is all digits."""
    ident = str(identifier or "").strip().lower()
    if not ident:
        return None
    rows = supabase.select("users", {"username": f"eq.{ident}"}, cols, 1)
    if not rows and ident.isdigit():
        rows = supabase.select("users", {"number": f"eq.{ident}"}, cols, 1)
    return rows[0] if rows else None


def public_user(u):
    return {k: u[k] for k in ("id", "name", "username", "number", "country_code")}


def _new_session(user_id, ip_address, user_agent):
    sid, raw, expires = str(uuid.uuid4()), new_token(), now() + SESSION_TTL
    supabase.insert("sessions", {
        "id": sid, "user_id": user_id, "expires_at": iso(expires),
        "ip_address": ip_address, "user_agent": (user_agent or "")[:300],
    })
    supabase.insert("tokens", {
        "user_id": user_id, "session_id": sid, "token_hash": sha256(raw),
        "type": "access", "expires_at": iso(expires),
    })
    return raw


def register(name, username, number, country_code, password, ip_address, user_agent):
    name = str(name or "").strip()
    username = str(username or "").strip().lower()
    number = re.sub(r"[\s-]", "", str(number or ""))
    country_code = str(country_code or "91").lstrip("+").strip()
    password = str(password or "")

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
    return _new_session(user["id"], ip_address, user_agent), public_user(user)


def login(identifier, password, ip_address, user_agent):
    user = find_user(identifier)
    password = str(password or "")
    if not user:
        hash_password(password)  # keep timing similar for unknown users
        raise ApiError(401, "Invalid username or password")
    if not verify_password(password, user["password_hash"]):
        raise ApiError(401, "Invalid username or password")
    return _new_session(user["id"], ip_address, user_agent), public_user(user)


def authenticate(raw_token):
    rows = supabase.select(
        "tokens",
        {"token_hash": f"eq.{sha256(raw_token)}", "type": "eq.access", "revoked_at": "is.null"},
        "*", 1,
    ) if raw_token else []
    if not rows or parse(rows[0]["expires_at"]) <= now():
        raise ApiError(401, "Not authenticated")
    users = supabase.select("users", {"id": f"eq.{rows[0]['user_id']}"}, USER_COLS, 1)
    if not users:
        raise ApiError(401, "Not authenticated")
    return users[0], rows[0]


def logout(token_row):
    stamp = {"revoked_at": iso(now())}
    supabase.update("tokens", {"id": f"eq.{token_row['id']}"}, stamp)
    if token_row.get("session_id"):
        supabase.update("sessions", {"id": f"eq.{token_row['session_id']}"}, stamp)


def revoke_user_access(user_id):
    """Log the user out everywhere (used after a password reset)."""
    stamp = {"revoked_at": iso(now())}
    supabase.update("tokens", {"user_id": f"eq.{user_id}", "revoked_at": "is.null"}, stamp)
    supabase.update("sessions", {"user_id": f"eq.{user_id}", "revoked_at": "is.null"}, stamp)


def forgot_password(identifier):
    """Send an OTP if the account exists. Silent no-op otherwise, so the caller
    can always show the same generic message and not reveal which accounts exist."""
    user = find_user(identifier)
    if not user:
        return

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
    code, rec_id = new_otp(), str(uuid.uuid4())
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


def verify_otp(identifier, code):
    bad = ApiError(400, "Invalid or expired code")
    user = find_user(identifier)
    code = str(code or "").strip()
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

    raw = new_token()
    supabase.insert("tokens", {
        "user_id": user["id"], "token_hash": sha256(raw), "type": "password_reset",
        "expires_at": iso(now() + RESET_TOKEN_TTL),
    })
    return raw


def reset_password(raw_token, new_password):
    new_password = str(new_password or "")
    if len(new_password) < 6:
        raise ApiError(400, "Password must be at least 6 characters")
    rows = supabase.select(
        "tokens",
        {"token_hash": f"eq.{sha256(raw_token)}", "type": "eq.password_reset", "revoked_at": "is.null"},
        "*", 1,
    ) if raw_token else []
    if not rows or parse(rows[0]["expires_at"]) <= now():
        raise ApiError(400, "Reset link expired. Please start again.")
    user_id = rows[0]["user_id"]
    supabase.update("users", {"id": f"eq.{user_id}"},
                     {"password_hash": hash_password(new_password), "updated_at": iso(now())})
    supabase.update("tokens", {"id": f"eq.{rows[0]['id']}"}, {"revoked_at": iso(now())})
    revoke_user_access(user_id)
