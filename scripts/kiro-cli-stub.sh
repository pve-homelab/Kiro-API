#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# kiro-cli STUB for offline / airgapped testing.
#
# Emulates just enough of the real kiro-cli surface that Kiro-API V2 uses:
#   kiro-cli chat --no-interactive        (reads prompt from stdin, echoes reply)
#   kiro-cli whoami                       (login status)
#   kiro-cli login --use-device-flow      (fake device flow)
#   kiro-cli --version
#
# Login state is faked via a marker file so `whoami` and the tray login button
# behave realistically. Set KIRO_STUB_LOGGED_IN=1 to force logged-in.
# ---------------------------------------------------------------------------
set -euo pipefail

STATE_DIR="${KIRO_STUB_STATE:-/tmp/kiro-stub}"
mkdir -p "$STATE_DIR"
MARKER="$STATE_DIR/logged_in"

cmd="${1:-}"; shift || true

case "$cmd" in
  --version|-V|version)
    echo "kiro-cli-stub 0.0.0 (offline test double)"
    ;;

  whoami)
    if [[ "${KIRO_STUB_LOGGED_IN:-0}" == "1" || -f "$MARKER" ]]; then
      echo "stub-user@example.com"
      exit 0
    fi
    echo "not logged in" >&2
    exit 1
    ;;

  login)
    # Emulate device-flow output, then mark logged in.
    echo "Attempting to automatically open the SSO authorization page..."
    echo "Confirm the following code in the browser: STUB-1234"
    echo "Open: https://example.com/device?code=STUB-1234"
    sleep 1
    touch "$MARKER"
    echo "Successfully logged in (stub)."
    ;;

  logout)
    rm -f "$MARKER"
    echo "Logged out (stub)."
    ;;

  chat)
    # Consume flags; the prompt arrives on stdin.
    model="auto"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --model) model="${2:-auto}"; shift 2;;
        --no-interactive) shift;;
        *) shift;;
      esac
    done
    prompt="$(cat || true)"
    # Simulate a little work so concurrency/queue behavior is observable.
    sleep "${KIRO_STUB_DELAY:-0.3}"
    # Echo a deterministic reply so tests can assert on it.
    last_line="$(printf '%s' "$prompt" | grep -a . | tail -n 1 || true)"
    echo "[stub:${model}] You said: ${last_line}"
    ;;

  *)
    echo "kiro-cli-stub: unknown command '$cmd'" >&2
    exit 2
    ;;
esac
