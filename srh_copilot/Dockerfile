FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Git on Windows does not preserve the execute bit, so set it here.
RUN chmod +x scripts/start_demo.sh \
    && useradd -m copilot && chown -R copilot /app
USER copilot

# 8000 = API, 8501 = Streamlit UI. docker compose publishes both.
EXPOSE 8000 8501

# Default: API only, which is what docker-compose.yml expects.
# The hosted single-container demo overrides this with scripts/start_demo.sh
# (see render.yaml), which runs the API on loopback and the UI on $PORT.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
