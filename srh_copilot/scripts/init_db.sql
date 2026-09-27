-- Executed automatically by the postgres container on first start (docker-compose mounts it).
CREATE EXTENSION IF NOT EXISTS vector;
-- Tables are created by the application on startup (core/vector_store.py, core/sessions.py).
-- Move to Alembic migrations before the first production deployment.
