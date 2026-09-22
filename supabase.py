"""Minimal Supabase (PostgREST) client — Python standard library only.

Credentials are set directly in this file (no .env needed).

IMPORTANT: the publishable key is meant for browsers and is restricted by Row Level Security.
db.sql turns RLS on for the private tables (users, sessions, tokens, otp_records) with no public
policies, so the publishable key can NOT read or write them. For the backend, paste your
*secret / service_role* key (Supabase Dashboard -> Project Settings -> API Keys) into
SECRET_KEY below. Never expose that key in the frontend.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

SUPABASE_URL = "https://jgzvffdybjvhbwdvgvtq.supabase.co"
PUBLISHABLE_KEY = "sb_publishable_1r7z61V1bJpTb06VDsbeJg_70LQ0PDy"
SECRET_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")  # or paste the secret key here as a string

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


def check():
    """Startup check: returns (ok, message)."""
    try:
        select("users", columns="id", limit=1)
        return True, "Supabase connected."
    except SupabaseError as e:
        if e.code == "PGRST205":
            return False, "Connected to Supabase, but tables are missing. Run db.sql in the SQL Editor."
        return False, f"Supabase problem: {e.message}"
