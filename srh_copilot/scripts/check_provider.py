"""Verify the configured LLM provider before ingesting or serving.

    python scripts/check_provider.py

Makes one tiny chat call and one tiny embedding call, then prints the real
embedding width so EMBEDDING_DIM can be set correctly for pgvector.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings  # noqa: E402
from core.providers import build_llm_and_embedder  # noqa: E402


async def main() -> int:
    print(f"provider: {settings.llm_provider}  embeddings: {settings.embedding_backend}")
    try:
        llm, embedder = build_llm_and_embedder(settings)
    except RuntimeError as exc:
        print(f"CONFIG ERROR: {exc}")
        return 1

    try:
        answer = await llm.chat([{"role": "user", "content": "Reply with the single word: ready"}],
                                max_tokens=16)
        print(f"chat ok: {answer.strip()[:80]}")
    except Exception as exc:
        print(f"CHAT FAILED: {type(exc).__name__}: {exc}")
        return 1

    try:
        vecs = await embedder.embed(["Stipendium für internationale Studierende"])
        dim = len(vecs[0])
        print(f"embeddings ok: {dim} dimensions")
        if dim != settings.embedding_dim:
            print(f"NOTE: set EMBEDDING_DIM={dim} in .env before switching VECTOR_BACKEND to pgvector")
    except Exception as exc:
        print(f"EMBEDDINGS FAILED: {type(exc).__name__}: {exc}")
        print("Tip: set EMBEDDING_BACKEND=local to embed on this machine instead "
              "(pip install sentence-transformers).")
        return 1

    print("all good, run: python scripts/ingest.py --agent student_service")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
