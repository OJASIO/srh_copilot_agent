#!/usr/bin/env bash
# In-house model server for the SRH Copilot on the SRH GPU cluster.
#
#   bash inhouse.sh setup    ONCE: Python env with vLLM 0.17+, download Qwen3.8-27B-FP8 and bge-m3
#   bash inhouse.sh start    EVERY SESSION: start the model server in the background
#   bash inhouse.sh test     check it answers, thinking is off, JSON schema works, embeddings load
#   bash inhouse.sh info     the values the Copilot's .env needs
#   bash inhouse.sh status | logs | stop
#
# Everything is stored in ~/vault/srh_inhouse (the persistent NFS mount), so
# setup survives between sessions. Override with SRH_ROOT=/some/path.
set -euo pipefail

ROOT="${SRH_ROOT:-$([[ -d "$HOME/vault" ]] && echo "$HOME/vault/srh_inhouse" || echo "$HOME/srh_inhouse")}"
VENV="$ROOT/venv"
MODELS="$ROOT/models"
LLM_REPO="Qwen/Qwen3.8-27B-FP8"      # Apache 2.0, not gated, about 27 GB
LLM_DIR="$MODELS/Qwen3.8-27B-FP8"
EMB_REPO="BAAI/bge-m3"               # MIT, 1024 dimensions
EMB_DIR="$MODELS/bge-m3"
PORT="${VLLM_PORT:-8001}"
URL="http://127.0.0.1:$PORT"
KEY_FILE="$ROOT/api_key"
PID_FILE="$ROOT/vllm.pid"
LOG="$ROOT/vllm.log"
GPU_UTIL="${GPU_UTIL:-0.45}"         # 0.45 x 141 GB = 63 GB; weights need 27 GB
MAX_LEN="${MAX_LEN:-32768}"          # our longest prompts are about 5k tokens
MAX_SEQS="${MAX_SEQS:-64}"           # parallel requests. Qwen3.8 is a hybrid (Mamba) model that needs one
                                     # cache block per request; vLLM's default of 1024 does not fit
export HF_HOME="$ROOT/hf_cache"
export UV_LINK_MODE=copy             # venv is on NFS, uv's cache on local disk: copy, don't hard-link

running() { [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; }
need_setup() { [[ -x "$VENV/bin/python" && -f "$LLM_DIR/config.json" ]] || { echo "run: bash inhouse.sh setup"; exit 1; }; }

vllm_ok() {
  "$VENV/bin/python" - <<'PY' 2>/dev/null
import sys, vllm
from packaging.version import Version
sys.exit(0 if Version(vllm.__version__.split("+")[0]) >= Version("0.17.0") else 1)
PY
}

setup() {
  mkdir -p "$MODELS" "$HF_HOME"
  if [[ ! -f "$KEY_FILE" ]]; then
    python3 -c 'import secrets; print(secrets.token_hex(24))' > "$KEY_FILE"
    chmod 600 "$KEY_FILE"
  fi

  echo "==== 1/3 Python environment: $VENV"
  if [[ ! -x "$VENV/bin/python" ]]; then
    # uv avoids Ubuntu's missing python3-venv package and installs much faster
    if command -v uv >/dev/null 2>&1; then uv venv --python python3 "$VENV"; else python3 -m venv "$VENV"; fi
  fi
  if vllm_ok; then
    echo "vLLM already installed: $("$VENV/bin/python" -c 'import vllm; print(vllm.__version__)')"
  else
    echo "installing vLLM 0.17+, PyTorch, CUDA libraries (several GB, 5 to 20 min, only once)"
    if command -v uv >/dev/null 2>&1; then
      uv pip install --python "$VENV/bin/python" "vllm>=0.17.0" "huggingface_hub[cli]" "sentence-transformers>=3.0" openai
    else
      "$VENV/bin/pip" install "vllm>=0.17.0" "huggingface_hub[cli]" "sentence-transformers>=3.0" openai
    fi
  fi

  echo "==== 2/3 models (about 29 GB, only once; re-running skips finished files)"
  "$VENV/bin/hf" download "$LLM_REPO" --local-dir "$LLM_DIR"
  "$VENV/bin/hf" download "$EMB_REPO" --local-dir "$EMB_DIR" --exclude "onnx/*"

  echo "==== 3/3 check"
  "$VENV/bin/python" - <<'PY'
import torch, vllm
print("vllm ", vllm.__version__)
print("torch", torch.__version__, "CUDA", torch.version.cuda)
p = torch.cuda.get_device_properties(0)
print("GPU  ", p.name, f"{p.total_memory / 2**30:.0f} GiB", f"compute {p.major}.{p.minor}")
PY
  du -sh "$LLM_DIR" "$EMB_DIR"
  echo
  echo "SETUP DONE. From now on, each session: bash inhouse.sh start"
}

start() {
  need_setup
  if running; then echo "already running (pid $(cat "$PID_FILE"))"; return 0; fi
  CAP=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d ' ')
  KV=()
  # FP8 KV cache: supported natively on Hopper (H100/H200, compute 9.0+)
  if awk "BEGIN{exit !($CAP >= 9.0)}"; then KV=(--kv-cache-dtype fp8); fi

  echo "starting Qwen3.8-27B-FP8 on $URL (GPU share $GPU_UTIL, max $MAX_LEN tokens, $MAX_SEQS parallel requests)"
  nohup "$VENV/bin/vllm" serve "$LLM_DIR" \
    --served-model-name srh-llm \
    --host 127.0.0.1 --port "$PORT" \
    --api-key "$(cat "$KEY_FILE")" \
    --max-model-len "$MAX_LEN" \
    --max-num-seqs "$MAX_SEQS" \
    --gpu-memory-utilization "$GPU_UTIL" \
    ${KV[@]+"${KV[@]}"} \
    --reasoning-parser qwen3 \
    --limit-mm-per-prompt '{"image": 0, "video": 0}' \
    >> "$LOG" 2>&1 &
  echo $! > "$PID_FILE"

  echo -n "loading (1 to 4 minutes)"
  for _ in $(seq 1 120); do
    if curl -sf -H "Authorization: Bearer $(cat "$KEY_FILE")" "$URL/v1/models" >/dev/null 2>&1; then
      echo; echo "READY at $URL/v1, model name srh-llm"; return 0
    fi
    if ! running; then echo; echo "vLLM stopped. Last lines of $LOG:"; tail -30 "$LOG"; exit 1; fi
    echo -n "."; sleep 5
  done
  echo; echo "not ready after 10 minutes, see: bash inhouse.sh logs"; exit 1
}

stop() {
  if running; then
    kill "$(cat "$PID_FILE")"; sleep 3
    if running; then kill -9 "$(cat "$PID_FILE")"; fi
  fi
  rm -f "$PID_FILE"; echo "stopped"
}

status() {
  if running; then echo "running (pid $(cat "$PID_FILE"))"; else echo "not running"; fi
  nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv
}

info() {
  echo "Put these in the Copilot's .env on this node:"
  echo "LLM_PROVIDER=selfhosted"
  echo "SELFHOSTED_BASE_URL=$URL/v1"
  echo "SELFHOSTED_MODEL=srh-llm"
  echo "SELFHOSTED_API_KEY=$(cat "$KEY_FILE" 2>/dev/null || echo '<run setup first>')"
  echo "EMBEDDING_BACKEND=local"
  echo "LOCAL_EMBEDDING_MODEL=$EMB_DIR"
  echo "LOCAL_EMBEDDING_DEVICE=cpu"
  echo "EMBEDDING_DIM=1024"
}

test_server() {
  need_setup
  running || { echo "server not running: bash inhouse.sh start"; exit 1; }
  URL="$URL" KEY="$(cat "$KEY_FILE")" EMB_DIR="$EMB_DIR" "$VENV/bin/python" - <<'PY'
import json, os, sys, time
from openai import OpenAI

c = OpenAI(base_url=os.environ["URL"] + "/v1", api_key=os.environ["KEY"], timeout=180)
off = {"chat_template_kwargs": {"enable_thinking": False}}
fails = []

t = time.perf_counter()
r = c.chat.completions.create(model="srh-llm", temperature=0.1, max_tokens=80, extra_body=off,
    messages=[{"role": "user", "content": "In one sentence: what is a Werkstudent in Germany?"}])
txt, n, dt = r.choices[0].message.content or "", r.usage.completion_tokens, time.perf_counter() - t
print(f"1 answer        {dt:.1f}s, {n / dt:.0f} tok/s: {txt.strip()[:120]}")
if not txt.strip() or "<think>" in txt:
    fails.append("answer/thinking")

schema = {"type": "object", "properties": {"overall_score": {"type": "integer"},
          "tier_1": {"type": "array", "items": {"type": "string"}}}, "required": ["overall_score", "tier_1"]}
r = c.chat.completions.create(model="srh-llm", temperature=0.2, max_tokens=300, extra_body=off,
    response_format={"type": "json_schema", "json_schema": {"name": "t", "schema": schema}},
    messages=[{"role": "user", "content": "Rate this CV line 1-10 and list errors: "
               "'Ich war verantwortlich für die datenpipeline, verbesserte Prozese um 30%'"}])
try:
    d = json.loads(r.choices[0].message.content)
    print(f"2 JSON schema   valid, score {d['overall_score']}, errors found: {d['tier_1']}")
except Exception as exc:
    print(f"2 JSON schema   FAIL {exc}"); fails.append("json")

from sentence_transformers import SentenceTransformer
m = SentenceTransformer(os.environ["EMB_DIR"], device="cpu")
v = m.encode(["STIBET for international students", "Internationale Studierende: STIBET",
              "Bibliothek Öffnungszeiten"], normalize_embeddings=True)
same, other = float(v[0] @ v[1]), float(v[0] @ v[2])
print(f"3 embeddings    dim {len(v[0])}, EN-DE same topic {same:.2f} vs unrelated {other:.2f}")
if len(v[0]) != 1024 or same <= other:
    fails.append("embeddings")

print("\nALL CHECKS PASSED" if not fails else f"\nFAILED: {fails}")
sys.exit(1 if fails else 0)
PY
}

case "${1:-}" in
  setup) setup ;;
  start) start ;;
  stop) stop ;;
  status) status ;;
  logs) tail -f "$LOG" ;;
  test) test_server ;;
  info) info ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
