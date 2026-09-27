#!/usr/bin/env bash
# The whole Copilot on the SRH GPU node, one command per session.
#
#   bash deploy/selfhosted/copilot.sh start    model server (if needed), provider check, ingest, API in the background
#   bash deploy/selfhosted/copilot.sh ui       Streamlit on 127.0.0.1:8501 (open it once jupyter-server-proxy exists)
#   bash deploy/selfhosted/copilot.sh eval     scholarship and CV evaluations against the in-house model
#   bash deploy/selfhosted/copilot.sh freeze   write requirements.lock.txt with the exact package versions
#   bash deploy/selfhosted/copilot.sh status | logs | stop | stop-all
#
# The model server itself is handled by inhouse.sh in the same folder (setup once,
# then `start` is called from here). The API and UI run from this project folder
# with the same venv as vLLM; logs and pid files live next to vllm.log.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "$HERE/../.." && pwd)"
ROOT="${SRH_ROOT:-$([[ -d "$HOME/vault" ]] && echo "$HOME/vault/srh_inhouse" || echo "$HOME/srh_inhouse")}"
VENV="$ROOT/venv"
API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-8501}"
API_PID="$ROOT/api.pid"
UI_PID="$ROOT/ui.pid"
API_LOG="$ROOT/api.log"
UI_LOG="$ROOT/ui.log"
export UV_LINK_MODE=copy
export COPILOT_API_URL="http://127.0.0.1:$API_PORT"

alive() { [[ -f "$1" ]] && kill -0 "$(cat "$1")" 2>/dev/null; }
need_venv() { [[ -x "$VENV/bin/python" ]] || { echo "no venv at $VENV: run bash $HERE/inhouse.sh setup"; exit 1; }; }

start() {
  need_venv
  bash "$HERE/inhouse.sh" start
  cd "$PROJECT"
  echo "==== provider check"
  "$VENV/bin/python" scripts/check_provider.py
  echo "==== knowledge index"
  "$VENV/bin/python" scripts/ingest.py --agent student_service
  if alive "$API_PID"; then
    echo "API already running (pid $(cat "$API_PID")); the index reloads by itself"
    return 0
  fi
  echo "==== API on $COPILOT_API_URL"
  nohup "$VENV/bin/uvicorn" main:app --host 127.0.0.1 --port "$API_PORT" >> "$API_LOG" 2>&1 &
  echo $! > "$API_PID"
  for _ in $(seq 1 60); do
    if curl -sf "$COPILOT_API_URL/health" >/dev/null 2>&1; then
      echo "API READY: $(curl -s "$COPILOT_API_URL/health")"
      return 0
    fi
    if ! alive "$API_PID"; then echo "API stopped. Last lines of $API_LOG:"; tail -30 "$API_LOG"; exit 1; fi
    sleep 2
  done
  echo "API not ready after 2 minutes, see: bash $0 logs"; exit 1
}

ui() {
  need_venv
  alive "$API_PID" || { echo "API not running: bash $0 start"; exit 1; }
  if alive "$UI_PID"; then echo "UI already running (pid $(cat "$UI_PID"))"; return 0; fi
  cd "$PROJECT"
  # Behind jupyter-server-proxy the origin is rewritten, which Streamlit's XSRF and
  # CORS checks reject on file upload. The API stays the security boundary.
  nohup "$VENV/bin/streamlit" run frontend/streamlit_app.py --server.address 127.0.0.1 \
    --server.port "$UI_PORT" --server.headless true \
    --server.enableXsrfProtection false --server.enableCORS false >> "$UI_LOG" 2>&1 &
  echo $! > "$UI_PID"
  sleep 4
  alive "$UI_PID" || { echo "UI stopped. Last lines of $UI_LOG:"; tail -20 "$UI_LOG"; exit 1; }
  echo "UI running on 127.0.0.1:$UI_PORT"
  echo "In the browser: <portal>/user/<your user>/proxy/$UI_PORT/ (needs jupyter-server-proxy in the image)"
}

run_eval() {
  need_venv
  cd "$PROJECT"
  "$VENV/bin/python" evaluation/run_eval.py evaluation/datasets/student_service_scholarship.jsonl
  "$VENV/bin/python" evaluation/run_cv_eval.py
  echo "Results are in $PROJECT/evaluation/results/ (grade the .md files)"
}

freeze() {
  need_venv
  uv pip freeze --python "$VENV/bin/python" > "$PROJECT/requirements.lock.txt"
  echo "wrote $PROJECT/requirements.lock.txt ($(wc -l < "$PROJECT/requirements.lock.txt") packages)"
}

stop_one() {
  if alive "$1"; then
    kill "$(cat "$1")"; sleep 2
    if alive "$1"; then kill -9 "$(cat "$1")"; fi
  fi
  rm -f "$1"
}

status() {
  bash "$HERE/inhouse.sh" status || true
  if alive "$API_PID"; then echo "API running (pid $(cat "$API_PID")): $(curl -s "$COPILOT_API_URL/health" || true)"
  else echo "API not running"; fi
  if alive "$UI_PID"; then echo "UI running (pid $(cat "$UI_PID")) on port $UI_PORT"; else echo "UI not running"; fi
}

case "${1:-}" in
  start) start ;;
  ui) ui ;;
  eval) run_eval ;;
  freeze) freeze ;;
  status) status ;;
  logs) tail -f "$API_LOG" ;;
  stop) stop_one "$UI_PID"; stop_one "$API_PID"; echo "API and UI stopped (model server still running)" ;;
  stop-all) stop_one "$UI_PID"; stop_one "$API_PID"; bash "$HERE/inhouse.sh" stop ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
