"""Loads a local .env.local (or .env) file into os.environ — standard library
only, no python-dotenv needed.

Real environment variables (e.g. ones set in Coolify) always win: this only
fills in values that aren't already set, so it's safe to import everywhere.
Import this before reading any env var, e.g. `import env` at the top of a
module, then `os.environ.get(...)`.
"""
import os

_loaded = False


def load():
    global _loaded
    if _loaded:
        return
    _loaded = True
    here = os.path.dirname(os.path.abspath(__file__))
    for name in (".env.local", ".env"):
        path = os.path.join(here, name)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                os.environ.setdefault(key, value)


load()
