"""Sends the 'verification code' template (WhatsApp/SMS campaign webhook)."""
import json
import os
import urllib.error
import urllib.request

import env  # noqa: F401 — loads .env.local into os.environ as a side effect

TEMPLATE_URL = os.environ.get(
    "OTP_TEMPLATE_URL",
    "https://campaignadmin.backendprod.com/webhook/template/88b40b82-5fca-4949-ac37-571432b9b879/process",
)


def send_otp(country_code, number, otp):
    """country_code e.g. '91'; number without country code. Raises RuntimeError on failure."""
    payload = {"countryCode": country_code, "receiver": number, "values": {"1": otp}}
    req = urllib.request.Request(
        TEMPLATE_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            if res.status >= 300:
                raise RuntimeError(f"OTP provider returned {res.status}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OTP provider returned {e.code}: {e.read()[:200]!r}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach OTP provider: {e.reason}")
