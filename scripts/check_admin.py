#!/usr/bin/env python3
"""Verify the admin endpoints the tray agent depends on."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
failures = []


def req(path, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    if headers:
        h.update(headers)
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    return urllib.request.urlopen(r, timeout=15)


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


# admin/config
cfg = json.loads(req("/admin/config").read())
check("admin config has v1_url", "v1_url" in cfg, cfg.get("v1_url", ""))

# enable auth, expect 401 without key, 200 with key
req("/admin/auth-key", "POST", {"key": "secret123"})
try:
    req("/v1/models")
    check("auth enforced (expect 401)", False, "no 401 raised")
except urllib.error.HTTPError as e:
    check("auth enforced (expect 401)", e.code == 401, str(e.code))

r = req("/v1/models", headers={"Authorization": "Bearer secret123"})
check("auth accepts valid key", r.status == 200)

# disable auth again
req("/admin/auth-key", "POST", {"key": ""})
check("auth disabled again", req("/v1/models").status == 200)

# login trigger (stub) + status poll
req("/admin/login", "POST", {})
time.sleep(2)
st = json.loads(req("/admin/login/status").read())
check("login produced output", bool(st.get("output")), (st.get("output") or "")[:60])

print()
if failures:
    print("FAILURES:", failures)
    sys.exit(1)
print("ADMIN CHECKS PASSED")
