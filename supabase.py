"""Minimal Supabase (PostgREST) client — Python standard library only.

Credentials come from environment variables — see .env.local for local dev
(loaded automatically by `import env` below; git-ignored, never committed) or
set them as real env vars in Coolify for deployment.

IMPORTANT: the publishable key is meant for browsers and is restricted by Row Level Security.
db.sql turns RLS on for the private tables (users, sessions, tokens, otp_records) with no public
policies, so the publishable key can NOT read or write them. The backend needs
SUPABASE_SECRET_KEY (Supabase Dashboard -> Project Settings -> API Keys, the
secret/service_role key). Never expose that key in the frontend.
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import env  # noqa: F401 — loads .env.local into os.environ as a side effect

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
JWKS_URL = os.environ.get("SUPABASE_JWKS_URL", "")  # not consumed yet — this backend uses its own token/session system, not Supabase Auth

API_KEY = SECRET_KEY or PUBLISHABLE_KEY


class SupabaseError(Exception):
    def __init__(self, status, message, code=None):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


def _request(method, table, params=None, body=None, prefer=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    if API_KEY.startswith("eyJ"):  # legacy JWT keys also go in Authorization; sb_* keys must not
        headers["Authorization"] = f"Bearer {API_KEY}"
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            raw = res.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
        except Exception:
            err = {}
        code = err.get("code")
        msg = err.get("message") or f"Supabase error {e.code}"
        if code == "42501" or e.code in (401, 403):
            msg = "Supabase denied access. Set the secret/service_role key in supabase.py (see db.sql)."
        raise SupabaseError(e.code, msg, code)
    except urllib.error.URLError as e:
        raise SupabaseError(503, f"Cannot reach Supabase: {e.reason}")


def select(table, filters=None, columns="*", limit=None, order=None):
    params = {"select": columns, **(filters or {})}
    if limit:
        params["limit"] = limit
    if order:
        params["order"] = order
    return _request("GET", table, params)


def insert(table, row):
    return _request("POST", table, body=row, prefer="return=representation")[0]


def update(table, filters, values):
    return _request("PATCH", table, filters, values, prefer="return=representation")


def delete(table, filters):
    return _request("DELETE", table, filters)


def _is_secret_key(key):
    if key.startswith("sb_secret_"):
        return True
    if key.startswith("eyJ"):  # legacy JWT-style key
        try:
            payload = json.loads(base64.urlsafe_b64decode(key.split(".")[1] + "=="))
            return payload.get("role") == "service_role"
        except Exception:
            return False
    return False


def check():
    """Startup check: returns (ok, message).

    RLS makes reads with the public key silently return an empty list instead of
    an error, which would otherwise look like a successful connection even though
    writes (register, login, contact...) will fail. So this checks the key type
    first, then confirms the tables exist.
    """
    if not SUPABASE_URL or not API_KEY:
        return False, "Missing SUPABASE_URL / SUPABASE_SECRET_KEY. Set them in .env.local (local) or as env vars (Coolify)."
    if not _is_secret_key(API_KEY):
        return False, (
            "Using the publishable key — writes will be blocked by Row Level Security. "
            "Set SECRET_KEY in supabase.py, or the SUPABASE_SERVICE_KEY env var, to your "
            "Supabase secret/service_role key (Project Settings -> API Keys)."
        )
    try:
        select("users", columns="id", limit=1)
        return True, "Supabase connected with the secret key."
    except SupabaseError as e:
        if e.code == "PGRST205":
            return False, "Connected to Supabase, but tables are missing. Run db.sql in the SQL Editor."
        return False, f"Supabase problem: {e.message}"
