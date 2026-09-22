"""Shared configuration and constants for the backend."""
import os
import re
from datetime import timedelta

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))

# Comma-separated list of allowed browser origins, e.g.
#   ALLOWED_ORIGINS=https://your-app.vercel.app,http://localhost:3000
# An entry starting with "*." matches any subdomain of that domain, which is
# useful for Vercel preview deployments, e.g. "*.vercel.app".
# ALLOWED_ORIGIN (singular) is still read for backward compatibility.
_default_origins = "http://localhost:3000,http://127.0.0.1:3000"
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", os.environ.get("ALLOWED_ORIGIN", _default_origins)).split(",")
    if o.strip()
]

SESSION_TTL = timedelta(days=7)
RESET_TOKEN_TTL = timedelta(minutes=15)
OTP_TTL = timedelta(minutes=10)
OTP_RESEND_WAIT = timedelta(seconds=60)
OTP_MAX_ATTEMPTS = 5

USERNAME_RE = re.compile(r"^[a-z0-9_.]{3,30}$")
USER_COLS = "id,name,username,number,country_code"
