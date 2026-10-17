#!/usr/bin/env python3
"""Admin helper for the SNAKE://ARENA play counters.

Counters live on abacus.jasoncameron.dev. The hub reads them with /get and
increments them with /hit on play. Admin keys are only issued once, at
/create time, so this script performs the (one-time) migration to a fresh
namespace and saves the keys locally:

    python3 counters.py migrate   # create every counter at 0 + save admin keys
    python3 counters.py status    # show current values (read-only)
    python3 counters.py reset     # set every counter back to 0 (needs admin keys)

Admin keys are written to counters.admin.json, which is git-ignored. Never
commit that file.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "snakes.json"
ADMIN_FILE = ROOT / "counters.admin.json"

BASE = "https://abacus.jasoncameron.dev"
NAMESPACE = "snake-arena-plays"
PACE = 0.5  # seconds between requests — the API allows 30 per 10s per IP


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface redirects instead of silently following them (POST /hit does)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def call(path: str, method: str = "GET", token: str | None = None, attempts: int = 3):
    """Return (status, parsed-json-or-None). Retries politely on 429."""
    url = BASE + path
    for attempt in range(attempts):
        request = urllib.request.Request(url, method=method)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        try:
            with _OPENER.open(request, timeout=15) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < attempts - 1:
                wait = _retry_after_ms(exc) / 1000
                print(f"    rate limited, waiting {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            return exc.code, None
        except Exception as exc:  # noqa: BLE001 - network hiccup, report and move on
            return 0, {"error": str(exc)}
    return 0, None


def _retry_after_ms(exc) -> float:
    raw = exc.headers.get("Retry-After") if exc.headers else None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 2000.0
    return value * 1000 if value < 100 else value


def keys() -> list[str]:
    return [entry["counter"] for entry in json.loads(MANIFEST.read_text(encoding="utf-8"))]


def load_admin() -> dict:
    if not ADMIN_FILE.exists():
        sys.exit(f"{ADMIN_FILE.name} not found — run: python3 counters.py migrate")
    return json.loads(ADMIN_FILE.read_text(encoding="utf-8"))


def migrate() -> int:
    admin: dict[str, str] = {}
    existing = {}
    if ADMIN_FILE.exists():
        existing = load_admin().get("keys", {})
    print(f"creating {len(keys())} counters in namespace '{NAMESPACE}'")
    for index, key in enumerate(keys(), 1):
        if key in existing:
            admin[key] = existing[key]
            print(f"  [{index:2}] {key:24} kept existing admin key")
            continue
        status, body = call(f"/create/{NAMESPACE}/{key}?initializer=0")
        if status == 201 and body:
            admin[key] = body["admin_key"]
            print(f"  [{index:2}] {key:24} created (value {body['value']})")
        elif status == 409:
            print(f"  [{index:2}] {key:24} already exists — no admin key available", file=sys.stderr)
        else:
            print(f"  [{index:2}] {key:24} FAILED (http {status})", file=sys.stderr)
        time.sleep(PACE)
    ADMIN_FILE.write_text(
        json.dumps(
            {
                "namespace": NAMESPACE,
                "created": time.strftime("%Y-%m-%d"),
                "note": "abacus admin keys — keep private, do not commit",
                "keys": admin,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"saved {len(admin)} admin keys -> {ADMIN_FILE.name} (git-ignored)")
    return 0


def status() -> int:
    total = 0
    for key in keys():
        code, body = call(f"/get/{NAMESPACE}/{key}")
        value = body["value"] if code == 200 and body else 0
        total += value
        print(f"  {key:24} {value:>6}")
        time.sleep(PACE)
    print(f"  {'TOTAL':24} {total:>6}")
    return 0


def reset() -> int:
    admin = load_admin()["keys"]
    for key in keys():
        code, body = call(f"/reset/{NAMESPACE}/{key}", method="POST", token=admin.get(key))
        value = body["value"] if code == 200 and body else "?"
        print(f"  {key:24} -> {value}" + ("" if code == 200 else f"  (http {code})"))
        time.sleep(PACE)
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "migrate":
        raise SystemExit(migrate())
    if command == "status":
        raise SystemExit(status())
    if command == "reset":
        raise SystemExit(reset())
    print(__doc__)
    raise SystemExit(2)
