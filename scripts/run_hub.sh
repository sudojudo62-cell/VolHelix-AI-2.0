#!/usr/bin/env bash
# Start the whole hub: VolHelix API (8000), dashboard (3000) and the Infinity Swarm Desk prototype (8080).
# Usage: scripts/run_hub.sh [path-to-INFINITY-SWARM-DESK]   (default: ../INFINITY-SWARM-DESK)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SWARM="${1:-$ROOT/../INFINITY-SWARM-DESK}"
pids=()
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT INT TERM

(cd "$ROOT" && python3 -m uvicorn backend.main:app --port 8000) & pids+=($!)
if [ -d "$SWARM/chatgpt_bot_2_0" ]; then
  (cd "$SWARM/chatgpt_bot_2_0" && python3 -m http.server 8080) & pids+=($!)
else
  echo "warn: $SWARM/chatgpt_bot_2_0 not found; Infinity Swarm Desk will show offline" >&2
fi
(cd "$ROOT/frontend" && npm run dev -- -p 3000) & pids+=($!)
echo "Hub: http://localhost:3000/hub"
wait
