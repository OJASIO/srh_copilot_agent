# In-house model server

Runs the language model on the SRH GPU cluster so no text leaves SRH. One
script, `inhouse.sh`, does everything on the server side.

| Command | When | What it does |
|---|---|---|
| `bash inhouse.sh setup` | once | Python env with vLLM 0.17+, downloads Qwen3.8-27B-FP8 (27 GB) and bge-m3 (2 GB). 20 to 40 min |
| `bash inhouse.sh start` | every session | Starts the model server in the background, waits until it answers (1 to 4 min) |
| `bash inhouse.sh test` | after start | Answer with thinking off, JSON forced by a schema, embeddings EN/DE |
| `bash inhouse.sh info` | when configuring | Prints the `.env` lines the Copilot needs |
| `bash inhouse.sh status \| logs \| stop` | any time | Control the server |

Everything is stored in `~/vault/srh_inhouse/` (venv, models, log, API key). The
vault is the persistent NFS mount; the rest of the home folder is the container
layer and can be reset. Setup runs once, then only `start` per session.

## Environment (checked 26 September 2026)

| | |
|---|---|
| Portal | JupyterLab in the browser, image **PyTorch (LLM Focus)**, node **Madrid** |
| GPU | NVIDIA H200 NVL, 140 GB, compute 9.0, not partitioned, driver 580 / CUDA 13.0 |
| Container | 32 cores, 96 GB RAM, Ubuntu 24.04, Python 3.12, `uv`, passwordless sudo, no Slurm |
| Storage | `~/vault` = NFS share, 1.7 TB free |
| Network | Hugging Face and PyPI reachable |
| Image vLLM | 0.13, too old for Qwen3.8, so setup installs 0.17+ into the vault |
| Browser access to ports | `jupyter-server-proxy` **not installed**: open question, see below |

## Choices

| Part | Choice | Why |
|---|---|---|
| Language model | `Qwen/Qwen3.8-27B-FP8` | Apache 2.0, not gated; FP8 runs natively on the H200 |
| Embeddings | `BAAI/bge-m3` on CPU, inside the Copilot API | MIT, 1024 dimensions, German and English; keeps the GPU for vLLM |
| Server | vLLM, OpenAI-compatible, `127.0.0.1:8001` | Our client code talks to it unchanged; schema-enforced JSON |
| GPU share | 45% (63 GB) | Weights need 27 GB; the rest is KV cache, far more than we use |
| Thinking | off | Qwen3.8 reasons before answering by default: slow, and useless for our tasks. The Copilot sends `enable_thinking: false` on every call |

## Running the Copilot on the node: `copilot.sh`

After `inhouse.sh setup` (once) and the `.env` below, one command per portal session:

| Command | What it does |
|---|---|
| `bash deploy/selfhosted/copilot.sh start` | `inhouse.sh start` if needed, provider check, ingest, API on `127.0.0.1:8000` in the background |
| `bash deploy/selfhosted/copilot.sh eval` | scholarship and CV evaluations against the in-house model, results in `evaluation/results/` |
| `bash deploy/selfhosted/copilot.sh ui` | Streamlit on `127.0.0.1:8501`, reachable once `jupyter-server-proxy` exists (see below) |
| `bash deploy/selfhosted/copilot.sh freeze` | writes `requirements.lock.txt` with the exact versions of the working venv |
| `bash deploy/selfhosted/copilot.sh status \| logs \| stop \| stop-all` | `stop` ends API and UI, `stop-all` also the model server |

Logs and pid files are next to `vllm.log` in `~/vault/srh_inhouse/`. Re-ingesting while the API runs
needs no restart: the API reloads the index file when it changes.

## Connecting the Copilot

The provider is built in: `LLM_PROVIDER=selfhosted` in `core/providers.py`. On the
node, `bash inhouse.sh info` prints the exact `.env` lines:

```
LLM_PROVIDER=selfhosted
SELFHOSTED_BASE_URL=http://127.0.0.1:8001/v1
SELFHOSTED_MODEL=srh-llm
SELFHOSTED_API_KEY=<generated at setup>
EMBEDDING_BACKEND=local
LOCAL_EMBEDDING_MODEL=/home/jovyan/vault/srh_inhouse/models/bge-m3
LOCAL_EMBEDDING_DEVICE=cpu
EMBEDDING_DIM=1024
```

Then re-run ingestion: vectors from Gemini and from bge-m3 cannot be compared.

## Open question: opening the UI in the browser

The browser reaches only JupyterLab. Streamlit on port 8501 needs `jupyter-server-proxy`,
which this image lacks. Options, best first:

1. Ask IT to add `jupyter-server-proxy` to the image. Standard JupyterHub extension; the UI
   is then at `<portal>/user/<name>/proxy/8501/`.
2. Install it ourselves (`sudo pip install jupyter-server-proxy`). Works only if the
   container survives a server restart. Test: `touch ~/persist_test` in one session,
   `ls ~/persist_test` in the next.
3. A notebook-based demo UI calling the API from JupyterLab directly. Needs no proxy.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `$'\r': command not found` | Windows line endings slipped in: `sed -i 's/\r$//' inhouse.sh` |
| `start` prints "vLLM stopped" | The last log lines show why. Out of memory: `GPU_UTIL=0.35 bash inhouse.sh start` |
| `max_num_seqs (...) exceeds available Mamba cache blocks` | Qwen3.8 is a hybrid model with one cache block per parallel request. Lower `MAX_SEQS` (default 64) or raise `GPU_UTIL` |
| `--limit-mm-per-prompt` rejected | Older syntax: replace the JSON with `image=0,video=0` in `inhouse.sh` |
| `--kv-cache-dtype` rejected | Delete the `KV=(--kv-cache-dtype fp8)` part in `inhouse.sh` |
| `import vllm` fails in a later session | The portal image changed. `rm -rf ~/vault/srh_inhouse/venv`, run `setup` again; models are kept |
| Server gone after a while | The portal ended the session. New session, `bash inhouse.sh start` |
