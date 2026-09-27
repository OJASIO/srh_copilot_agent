"""Scaffold a new plug from the template:  python scripts/new_agent.py hr_compliance "HR Compliance" """
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if len(sys.argv) < 3:
    sys.exit("usage: new_agent.py <agent_id> <Display Name>")
agent_id, name = sys.argv[1], sys.argv[2]
if not re.fullmatch(r"[a-z][a-z0-9_]*", agent_id):
    sys.exit("agent_id must be snake_case")
dst = ROOT / "agents" / agent_id
if dst.exists() and any(dst.glob("manifest.yaml")):
    sys.exit(f"{dst} already has a manifest")
shutil.copytree(ROOT / "agents" / "future_agent_template", dst, dirs_exist_ok=True)
for p in dst.rglob("*.*"):
    if p.suffix in {".py", ".yaml", ".md"}:
        txt = p.read_text(encoding="utf-8").replace("future_agent_template", agent_id).replace("Template Agent", name)
        p.write_text(txt, encoding="utf-8")
(ROOT / "data" / "raw" / agent_id / "general").mkdir(parents=True, exist_ok=True)
print(f"created agents/{agent_id}. Now add '{agent_id}' to config/agents.yaml under enabled.")
