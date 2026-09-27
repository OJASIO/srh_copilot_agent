#!/usr/bin/env bash
# Single-container entrypoint for the hosted prototype (Render, Fly, Cloud Run).
#
# The platform gives us exactly one public port ($PORT). Streamlit takes it;
# the API stays on loopback. The frontend still talks to the API over HTTP and
# knows nothing else about it, so splitting them into two services later is a
# change of one environment variable (COPILOT_API_URL) and nothing else.
set -euo pipefail

API_PORT="${API_PORT:-8000}"
PUBLIC_PORT="${PORT:-8501}"
export COPILOT_API_URL="http://127.0.0.1:${API_PORT}"

echo "[start] building the vector index (about 55 chunks, a few seconds)"
python scripts/ingest.py --agent student_service || echo "[start] ingest failed; scholarship answers will have no sources"

echo "[start] API on ${COPILOT_API_URL}"
uvicorn main:app --host 127.0.0.1 --port "${API_PORT}" --log-level info &
API_PID=$!
trap 'kill ${API_PID} 2>/dev/null || true' EXIT

python - <<'PY'
import os, sys, time, urllib.request
url = os.environ["COPILOT_API_URL"] + "/health"
for _ in range(60):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            if r.status == 200:
                print("[start] API healthy"); sys.exit(0)
    except Exception:
        time.sleep(1)
print("[start] API did not become healthy in 60s; starting UI anyway", file=sys.stderr)
PY

echo "[start] UI on 0.0.0.0:${PUBLIC_PORT}"
# The platform's TLS proxy rewrites the origin, which Streamlit's XSRF check rejects
# on file upload. Off for this deployment only (see .streamlit/config.toml).
exec streamlit run frontend/streamlit_app.py \
    --server.port "${PUBLIC_PORT}" \
    --server.address 0.0.0.0 \
    --server.enableXsrfProtection false
