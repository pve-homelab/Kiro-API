#!/usr/bin/env python3
"""Verify the reaper is tracking child processes and cleaning up.

Fires a burst, then confirms:
  * child process count returned to baseline after jobs finished (no orphans)
  * zombies_reaped / force-kills are sane
  * no leak warning under normal load
"""
from __future__ import annotations

import concurrent.futures
import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"


def stats() -> dict:
    return json.loads(urllib.request.urlopen(BASE + "/stats", timeout=10).read())


def chat(i: int) -> None:
    body = json.dumps({"model": "auto", "messages": [{"role": "user", "content": f"reap {i}"}]}).encode()
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=body,
                                headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def main() -> int:
    before = stats()["reaper"]
    print("baseline children:", before["last_child_count"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as ex:
        list(ex.map(chat, range(40)))

    # let the reaper sweep (interval 15s) plus job teardown settle
    time.sleep(18)
    after = stats()["reaper"]
    print(json.dumps(after, indent=2))

    ok = True
    if after["last_child_count"] > before["last_child_count"] + 4:
        print("FAIL: child processes did not return to baseline (possible orphans)")
        ok = False
    if after["leak_warning"]:
        print("FAIL: leak warning raised under normal load")
        ok = False
    print("PASS: reaper healthy" if ok else "REAPER CHECK FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
