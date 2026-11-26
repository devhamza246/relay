#!/usr/bin/env bash
# Start (or stop) a temporary, loopback-only API that calls the real model and records every response to a cassette.
# It is separate from the public deployment, which never holds a key.
#
#   ANTHROPIC_API_KEY=... scripts/bench_live.sh start      # spends money, capped at $RELAY_BENCH_CAP (default 0.60) per day
#   python -m eval.runner --url http://127.0.0.1:8826 --engine live --out eval/results/live.json
#   cp /tmp/relay-bench/cassette.jsonl eval/cassette.jsonl && scripts/bench_live.sh stop
set -euo pipefail
cd "$(dirname "$0")/.."
NAME=relay-bench-api
DIR=/tmp/relay-bench
case "${1:-}" in
  start)
    : "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}"
    APP_PASSWORD=$(sed -n 's/^RELAY_APP_DB_PASSWORD=//p' .env)
    mkdir -p "$DIR" && chmod 777 "$DIR"
    export RELAY_DATABASE_URL="postgresql://relay_app:${APP_PASSWORD}@postgres:5432/relay"
    docker run -d --rm --name "$NAME" --network relay_default -p 127.0.0.1:8826:8000 -v "$DIR:/data" \
      -e ANTHROPIC_API_KEY -e RELAY_DATABASE_URL -e RELAY_REDIS_URL=redis://redis:6379/1 -e RELAY_LLM_MODE=record \
      -e RELAY_CASSETTE=/data/cassette.jsonl -e RELAY_DAILY_SPEND_CAP_USD="${RELAY_BENCH_CAP:-0.60}" \
      -e RELAY_RATE_LIMIT_PER_MIN=0 -e RELAY_RETENTION_HOURS=0 relay-app:latest >/dev/null
    for _ in $(seq 60); do curl -sf localhost:8826/healthz >/dev/null && echo "bench API ready on 127.0.0.1:8826" && exit 0; sleep 1; done
    echo "bench API did not become healthy" >&2; docker logs "$NAME" 2>&1 | tail -20; exit 1 ;;
  stop) docker rm -f "$NAME" >/dev/null && echo stopped ;;
  *) echo "usage: $0 start|stop" >&2; exit 2 ;;
esac
