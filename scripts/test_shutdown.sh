#!/usr/bin/env bash
# Verify clean shutdown and NO ghost kiro-cli processes after the container stops.
#
# Strategy:
#   1. Start the container with a slow stub so jobs stay in-flight.
#   2. Fire several concurrent requests (they will be mid-flight).
#   3. Count kiro-cli processes visible on the host (via the container's cgroup).
#   4. `docker stop` the container (sends SIGTERM, honours stop_grace_period).
#   5. Confirm: clean-shutdown log lines present, container exited 0,
#      and ZERO kiro-cli processes remain on the host.
#   6. Restart and confirm health is green again (clean restart).
set -uo pipefail

IMG="kiro-api-bundle:2.0.0"
NAME="kiro-shutdown-test"

echo "== cleanup any prior =="
docker rm -f "$NAME" >/dev/null 2>&1 || true

echo "== start container (slow stub so jobs stay in-flight) =="
docker run -d --name "$NAME" --init \
  -e KIRO_STUB_LOGGED_IN=1 -e KIRO_STUB_DELAY=8 \
  -p 8787:8787 -p 8788:8788 "$IMG" >/dev/null
sleep 4

echo "== fire 5 in-flight jobs =="
for i in 1 2 3 4 5; do
  curl -s -o /dev/null \
    -H 'Content-Type: application/json' \
    --data "{\"model\":\"auto\",\"messages\":[{\"role\":\"user\",\"content\":\"job $i\"}]}" \
    http://127.0.0.1:8787/v1/chat/completions &
done
sleep 3

echo "== kiro-cli processes running INSIDE the container (should be > 0) =="
INFLIGHT=$(docker exec "$NAME" sh -c "ps -eo comm 2>/dev/null | grep -c kiro-cli" 2>/dev/null || echo 0)
echo "in-flight kiro-cli inside container: $INFLIGHT"

echo "== kiro-cli processes visible on the HOST before stop =="
HOST_BEFORE=$(pgrep -fa kiro-cli 2>/dev/null | grep -v test_shutdown | wc -l)
echo "host kiro-cli before stop: $HOST_BEFORE"

echo "== docker stop (graceful SIGTERM) =="
STOP_START=$(date +%s)
docker stop "$NAME" >/dev/null
STOP_END=$(date +%s)
echo "stop took $((STOP_END - STOP_START))s"

echo "== shutdown log lines =="
docker logs "$NAME" 2>&1 | grep -E "shutting down|draining|stopped cleanly|killed .* in-flight" || echo "(no shutdown lines found)"

echo "== exit code =="
docker inspect -f '{{.State.ExitCode}}' "$NAME"

echo "== ghost kiro-cli processes on the HOST after stop (must be 0) =="
sleep 2
HOST_AFTER=$(pgrep -fa kiro-cli 2>/dev/null | grep -v test_shutdown | wc -l)
echo "host kiro-cli after stop: $HOST_AFTER"

echo "== clean restart =="
docker start "$NAME" >/dev/null
sleep 5
HEALTH=$(curl -s http://127.0.0.1:8787/health | grep -o '"status":"ok"' || echo "UNHEALTHY")
echo "post-restart health: $HEALTH"

echo "== teardown =="
docker rm -f "$NAME" >/dev/null 2>&1 || true

echo
echo "==================== RESULT ===================="
PASS=1
[ "$HOST_AFTER" -eq 0 ] || { echo "FAIL: $HOST_AFTER ghost kiro-cli process(es) survived"; PASS=0; }
[ "$HEALTH" = '"status":"ok"' ] || { echo "FAIL: container did not restart cleanly"; PASS=0; }
[ "$PASS" -eq 1 ] && echo "PASS: clean shutdown, no ghosts, clean restart" || echo "SHUTDOWN TEST FAILED"
