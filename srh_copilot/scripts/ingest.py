"""Ingest raw documents for one or all agents into the vector store.

    python scripts/ingest.py --agent student_service
    python scripts/ingest.py --all
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings  # noqa: E402
from core.logging_config import setup_logging  # noqa: E402
from core.services import ServiceContainer  # noqa: E402
from ingestion.pipeline import RAW_DIR, ingest_agent  # noqa: E402


async def main(agents: list[str]):
    services = ServiceContainer.build(settings)
    await services.startup()
    for agent_id in agents:
        stats = await ingest_agent(services, agent_id)
        for coll, n in stats.items():
            print(f"{coll}: {n} chunks")


if __name__ == "__main__":
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    targets = [p.name for p in RAW_DIR.iterdir() if p.is_dir()] if args.all else args.agent
    if not targets:
        ap.error("give --agent <id> or --all")
    asyncio.run(main(targets))
