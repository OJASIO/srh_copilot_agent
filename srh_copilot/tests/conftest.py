import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# The suite is offline whatever the local .env says (on the GPU node it names the
# self-hosted model and bge-m3): environment variables win over .env values.
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("EMBEDDING_BACKEND", "mock")
os.environ.setdefault("VECTOR_BACKEND", "memory")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("API_KEY", "test-key")
