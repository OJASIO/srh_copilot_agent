# SRH AI Copilot

Unified academic AI platform for SRH University with plug-and-play domain agents.
Implements the architecture proposed for the case study (supervision: Prof. Dr. Swati Chandna):

| Architecture layer (deck slide 4)   | Where it lives in this repo                                   |
|-------------------------------------|---------------------------------------------------------------|
| Multi-Channel Interaction Layer     | `main.py` (REST API), `frontend/streamlit_app.py` (chat UI)   |
| AI Orchestration Layer              | `core/`: `agent_router.py` (Routing Engine), `auth.py` (Authentication), `orchestrator.py` (Workflow Engine), `prompts.py` (Prompt Management), `guardrails.py` (Guardrails for Ethical AI) |
| Domain Agents (plugs)               | `agents/<agent_id>/` with `manifest.yaml` + `agent.py` + `tasks/` |
| Shared AI Services                  | `core/providers.py` (LLM + embedding vendors), `core/retrieval.py` (RAG), `core/services.py` (`ServiceContainer`) |
| Knowledge & Data Layer              | `core/vector_store.py` (pgvector / memory), `ingestion/`, `data/raw/` |

## Quick start

A ready-to-run `.env` ships with this folder, already set to Gemini. It is git-ignored,
so it stays on your machine and is never committed.

1. Open `.env` and paste your free Gemini key (https://aistudio.google.com/apikey)
   after `GEMINI_API_KEY=`. That is the only edit needed.
2. Double-click `setup.bat` once, to build the virtual environment and install dependencies.
3. Double-click `start_api.bat`. It checks the provider, builds the knowledge index and
   serves the API on http://localhost:8000.
4. Double-click `start_ui.bat` in a second window. UI on http://localhost:8501.

Never edit `.env.example`. It is the blank template that ships with the repo for the next
person who clones it; the application reads `.env` only.

Same thing by hand, on any platform:

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # then paste your key into .env
python scripts/check_provider.py                         # verify the configured provider
python scripts/ingest.py --agent student_service         # build the knowledge index
uvicorn main:app --reload                                # API on http://localhost:8000/docs
streamlit run frontend/streamlit_app.py                  # UI on http://localhost:8501
pytest                                                   # offline test suite, no key needed
```

Set `LLM_PROVIDER=mock` in `.env` to run the whole stack offline with no key at all.

## Choosing a model provider

Set `LLM_PROVIDER` in `.env`. All of them go through the same `openai` client class, so
there is no per-vendor SDK and no agent code changes.

| `LLM_PROVIDER` | Keys needed in `.env`           | Notes                                                        |
|----------------|----------------------------------|--------------------------------------------------------------|
| `mock`         | none                             | Offline placeholder answers. Default, used by the test suite. |
| `gemini`       | `GEMINI_API_KEY`                 | Free tier. Key from https://aistudio.google.com/apikey. Uses Google's OpenAI-compatible endpoint. Optional `GEMINI_REASONING_EFFORT=none` turns thinking off, for a fair comparison with the in-house model. |
| `selfhosted`   | `SELFHOSTED_API_KEY`, `SELFHOSTED_BASE_URL` | vLLM on the SRH GPU node (Qwen3.8-27B-FP8), nothing leaves SRH. Needs `EMBEDDING_BACKEND=local` (bge-m3). Setup: `deploy/selfhosted/README.md`; every session: `bash deploy/selfhosted/copilot.sh start` |
| `openai`       | `OPENAI_API_KEY`                 | Needs billing credit; a new key with no credit returns `insufficient_quota`. |
| `azure`        | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, deployments | EU data residency, the production option for the university. |

Embeddings are configured separately with `EMBEDDING_BACKEND`:

- `remote` (default) calls the embedding API of whichever vendor `LLM_PROVIDER` names.
- `local` runs `sentence-transformers` on this machine. No API cost, no rate limit,
  and document text never leaves the server, which is the stronger GDPR position.
  Needs `pip install sentence-transformers` and downloads the model on first use.
- `mock` is offline hashing, for tests only.

After changing provider or embedding backend, re-run `python scripts/ingest.py --agent student_service`.
Vectors from different models are not comparable, so a stale index returns nonsense.
`scripts/check_provider.py` prints the real embedding width; set `EMBEDDING_DIM` to that
value before switching `VECTOR_BACKEND` to `pgvector`.

## Naming conventions

The top-level folder structure is fixed by the project brief and is not changed.
Everything created inside it follows these rules, so a reviewer or a new contributor
can predict where something lives and what it is called.

- Modules are `snake_case` with words separated (`agent_base.py`, `vector_store.py`,
  `logging_config.py`), classes are `PascalCase`, settings, ids and JSON fields are `snake_case`.
- `core/` module names mirror the architecture deck's boxes so code and diagram can be read
  side by side. Where the deck and common engineering usage differ, the code uses the common
  term and the table above records the mapping (`orchestrator.py` is the deck's Workflow Engine).
- Abstractions are prefixed `Base` (`BaseAgent`, `BaseTask`, `BaseLLM`, `BaseVectorStore`);
  implementations are named after their technology (`PgVectorStore`, `InMemoryVectorStore`,
  `OpenAICompatibleLLM`, `LocalEmbedder`), never after their role in the story.
- `*_provider` settings choose an external vendor (`LLM_PROVIDER=gemini`). `*_backend` settings
  choose where work happens or where data lives (`EMBEDDING_BACKEND=local`, `VECTOR_BACKEND=pgvector`).
  The two words are not interchangeable.
- One word, one meaning. "Memory" is only ever `InMemory*`, meaning held in RAM; conversation
  history lives in `core/sessions.py` and is called a session throughout.
- Agent ids, task ids and knowledge collection names are the same strings in the manifest,
  the API, the UI and the `data/raw/` folder tree, so a name can be grepped end to end.

## Production path

```bash
cp .env.example .env   # set APP_ENV=prod, real API_KEY, 32+ char JWT_SECRET, LLM keys
docker compose up --build
docker compose exec api python scripts/ingest.py --all
```

`docker compose` starts Postgres with pgvector (chunks, messages, audit_log), the API and the UI.
In `APP_ENV=prod` the API refuses to start with placeholder secrets, the mock LLM or the memory store,
and the demo token issuer and `/docs` are disabled. Remaining production work is listed below.

## Hosting a demo

`DEPLOY.md` covers putting a shareable URL online: one container running the API on loopback and the
Streamlit UI on the public port (`scripts/start_demo.sh`), a Render blueprint (`render.yaml`), a
shared-password gate, and what the free tier costs you in cold starts. Read the first section before
deploying: the **free Gemini tier forbids submitting personal data**, so a hosted prototype takes test
CVs only until the project moves to the paid tier.

## How the plug board works

```
config/agents.yaml  ->  core/registry.py  ->  agents/<id>/manifest.yaml + agent.py
      (enabled list)      (discover, load,        (what the agent offers: tasks,
       or ENABLED_AGENTS   hot plug/unplug)         routing keywords, collections)
```

- Plug in: create the folder (`python scripts/new_agent.py <id> "<Name>"`), add the id to `config/agents.yaml`.
- Unplug: remove the id from `config/agents.yaml` (or `POST /admin/agents/<id>/unplug` at runtime).
- A broken or unlisted agent never crashes the platform; it shows up in `/health` under `load_errors`.
- Agents receive everything through `core.services.ServiceContainer` (LLM, embedder, retriever, prompts, guardrails,
  session store) and only implement `BaseTask.run()`. Swapping the LLM vendor or the vector database
  is a config change, no agent code changes (the "prevents vendor lock-in" requirement).

Every request follows the same pipeline in `core/orchestrator.py`:
`guardrails.check_input -> history -> route -> agent.handle -> guardrails.check_output -> persist + audit`.

## Agents and tasks

An agent can expose several tasks. The UI reads them from `GET /agents` and shows a task choice when
there is more than one. Student Service is the first plug:

| Agent            | Task id            | UI label                 | Status                                  |
|------------------|--------------------|--------------------------|-----------------------------------------|
| student_service  | cv_check           | CV Check                 | tiered EN/DE review + ATS score, personal data masked first, follow-up questions on the review; logic in `agents/student_service/cv_check/` |
| student_service  | scholarship_info   | Scholarship Information  | RAG over `data/raw/student_service/scholarship/`, follow-up aware, responsible office per programme |

Task selection precedence: UI selection > attached file (CV) > keyword rule > LLM classifier.

A task can declare extra input fields in its manifest (`fields`: text, textarea or select). The UI
renders them without task-specific code and sends the values as `inputs`; CV Check uses this for the
review language and the job description.

## API

| Method | Path                            | Auth                    | Purpose                                  |
|--------|---------------------------------|-------------------------|------------------------------------------|
| GET    | /health                         | none                    | status, plugged agents, load errors      |
| POST   | /auth/token                     | API key                 | demo JWT (dev only; SSO in production)   |
| GET    | /agents                         | API key                 | enabled agents and their tasks (drives the UI) |
| POST   | /chat                           | API key + optional JWT  | `{message, session_id?, agent_id?, task_id?, inputs?}` |
| POST   | /chat/upload                    | API key + optional JWT  | same as /chat, multipart with `file`; `inputs` as a JSON string |
| GET    | /admin/agents                   | API key + admin role    | enabled vs available plugs               |
| POST   | /admin/agents/{id}/plug|unplug  | API key + admin role    | hot plug without restart                 |

## Repository layout

```
agents/          domain agents (plugs); future_agent_template/ is the starting point
config/          settings.py (env), agents.yaml (plug board)
core/            orchestration layer + shared AI services
data/            raw/<agent>/<collection>/ sources, processed/ derived files
evaluation/      run_eval.py + datasets/*.jsonl
frontend/        Streamlit client (replaceable, talks only to the API)
ingestion/       document loaders, chunking, vector store loading
logs/            rotating app.log (PII redacted)
notebooks/       experiments
scripts/         check_provider.py, ingest.py, new_agent.py, init_db.sql, smoke_test.sh
tests/           pytest suite (offline)
main.py          FastAPI application
.env             your private settings (git-ignored); the app reads this
.env.example     blank template that ships with the repo; the app never reads this
setup.bat        Windows: create .venv and install dependencies (run once)
start_api.bat    Windows: check provider, ingest, serve the API
start_ui.bat     Windows: serve the Streamlit UI
```

## Security measures in place

- **Access:** API key on every non-health route; JWT with roles (student, staff, hr, admin) and role-guarded
  admin routes; rate limit per signed-in user or browser session (not per IP, which behind the UI is one
  address for everyone); request bodies above the upload limit are rejected while they arrive.
- **Personal data** (`core/pii.py`, one set of detectors for the whole platform): emails, phone numbers
  (also "0151/1234567"), IBANs and matriculation numbers are masked in chat messages and task fields
  before any model sees them, and therefore also in the stored history and the audit trail. Log lines are
  redacted without touching their timestamps. File names are never logged.
- **CV anonymiser** (`agents/student_service/cv_check/anonymiser.py`): name (also behind a LEBENSLAUF
  title, in the Word page header, and the surname alone), contact data, date and place of birth,
  nationality, marital status, religion and similar details, addresses anywhere in the CV. The six
  evaluation CVs leak 0 of 46 planted personal values (v5 before these fixes: 13).
- **Prompt injection:** English and German patterns on normalised text (invisible characters removed)
  block chat messages; in documents (CV, job description) the sentences are removed and reported as a
  critical finding instead; documents and retrieved context sit in delimited blocks the prompt declares as
  data; the review JSON is validated and clamped; an answer that repeats a system prompt is replaced.
- **Sessions** belong to the user who created them; a known session id reveals nothing to someone else.
- Security headers; non-root Docker user; prod startup check that rejects placeholder secrets; Streamlit's
  XSRF protection on except behind a rewriting proxy (demo host, JupyterHub).

Internal documents (AC5 manual) should go into a separate collection restricted to staff roles before ingestion.

## Evaluation

| Harness | What it measures |
|---|---|
| `python evaluation/run_eval.py evaluation/datasets/student_service_scholarship.jsonl` | 29 scholarship questions (English, German, follow-ups, out of scope, injection, personal data). The automatic screen grades the model's own text, not the contact block and disclaimer the code appends. Human grades in the `.md` are the accuracy number. |
| `python evaluation/run_cv_eval.py` | Six generated test CVs (`evaluation/cv_cases.py`): PII leakage, over-masking, planted-error recall, clean-CV noise, JSON validity, language detection, injection, latency. Leakage is exact with any provider, including mock. |

On the GPU node, `bash deploy/selfhosted/copilot.sh eval` runs both against the in-house model.

## Roadmap to production

1. Replace the demo token issuer with the university SSO (Microsoft Entra ID, OIDC) issuing the same claims.
2. Alembic migrations instead of `CREATE TABLE IF NOT EXISTS` on startup.
3. Role-based collection access (staff-only knowledge) enforced in `Retriever.search`.
4. LLM-based guardrail classifier (toxicity, off-topic) in addition to the regex rules.
5. Observability: request tracing (OpenTelemetry), eval runs in CI with a real model.
6. Reverse proxy with TLS (Caddy or nginx) in front of API and UI; secrets from a vault, not `.env`.
7. React or eCampus (Moodle) embedding of the chat once the API is stable.

## Future scope: knowledge base maintenance by staff

Today knowledge is added by placing a file in `data/raw/<agent>/<collection>/` and running
`python scripts/ingest.py --agent <agent>`. Two extensions would let Student Service keep it current
without touching the server:

1. **Sources list with automatic refresh.** A file (for example `config/sources.yaml`) listing public
   URLs per collection, plus a scheduled script that fetches each page, converts it to text, and
   re-ingests the collection. Covers pages that change every semester, such as the financing page.
2. **Upload page for staff.** An admin-only screen where staff upload a document into a collection and
   it is ingested immediately. The admin role checks already exist in the API (`require_roles`).

Both need a review step before content goes live, because anything in a collection can be quoted to
students (see the internal-only notes found in the hotline FAQ).
