#!/usr/bin/env python3
"""End-to-end smoke test for Kiro-API V2.

Exercises the OpenAI-compatible endpoints plus health/stats and a small
concurrency burst. Run it against a running instance:

    python3 scripts/smoke_test.py http://127.0.0.1:8787

Exit code is non-zero if any check fails.
"""
from __future__ import annotations

import concurrent.futures
import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
failures: list[str] = []


def _req(path: str, method: str = "GET", body: dict | None = None, stream: bool = False):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    resp = urllib.request.urlopen(req, timeout=30)
    if stream:
        return resp
    return json.loads(resp.read().decode())


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def main() -> int:
    # health
    h = _req("/health")
    check("health status ok", h.get("status") == "ok", h.get("status", ""))
    check("health has pool", "pool" in h)
    check("health has reaper", "reaper" in h)

    # models
    m = _req("/v1/models")
    check("models list", m.get("object") == "list" and len(m.get("data", [])) >= 1)

    # chat sync
    c = _req("/v1/chat/completions", "POST", {
        "model": "auto",
        "messages": [{"role": "user", "content": "hello there"}],
    })
    content = c.get("choices", [{}])[0].get("message", {}).get("content", "")
    check("chat sync returns content", "hello there" in content, content[:80])
    check("chat usage present", c.get("usage", {}).get("total_tokens", 0) > 0)

    # chat stream
    resp = _req("/v1/chat/completions", "POST", {
        "model": "auto",
        "stream": True,
        "messages": [{"role": "user", "content": "stream test"}],
    }, stream=True)
    raw = resp.read().decode()
    check("stream has DONE", "[DONE]" in raw)
    check("stream has content delta", "stream test" in raw)
    check("stream has usage chunk", '"usage"' in raw)

    # legacy completions
    lc = _req("/v1/completions", "POST", {"model": "auto", "prompt": "legacy prompt"})
    check("legacy completion text", "legacy prompt" in lc.get("choices", [{}])[0].get("text", ""))

    # concurrency burst
    def one(i: int) -> bool:
        try:
            r = _req("/v1/chat/completions", "POST", {
                "model": "auto",
                "messages": [{"role": "user", "content": f"burst {i}"}],
            })
            return f"burst {i}" in r["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa
            print("   burst error:", exc)
            return False

    n = 30
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(one, range(n)))
    dt = time.time() - t0
    check(f"concurrency burst {n} ok", all(results), f"{sum(results)}/{n} in {dt:.1f}s")

    # stats reflect the burst
    s = _req("/stats")
    check("stats counted requests", s["pool"]["total_requests"] >= n + 3)
    check("no pool errors", s["pool"]["total_errors"] == 0, str(s["pool"]["total_errors"]))

    print()
    if failures:
        print(f"FAILURES: {failures}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
