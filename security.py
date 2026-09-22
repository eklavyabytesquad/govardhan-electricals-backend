"""Password hashing, tokens and time helpers — standard library only."""
import hashlib
import secrets
from datetime import datetime, timezone


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
    import hmac
    salt_hex, _ = stored.split("$")
    return hmac.compare_digest(hash_password(password, bytes.fromhex(salt_hex)), stored)


def new_token():
    return secrets.token_urlsafe(32)


def new_otp():
    return f"{secrets.randbelow(1_000_000):06d}"
