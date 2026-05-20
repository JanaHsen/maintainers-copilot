FROM python:3.12-slim

# uv binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# System packages (curl for healthcheck; build-essential not needed because
# torch/transformers wheels are CPU-only and prebuilt for linux/amd64).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Build context is the repo root so we can share pyproject.toml/uv.lock and
# the app.infra.* modules (vault_client, log_redaction) the model server
# reuses. Dependencies in the cached layer; the `ml` group pins
# transformers==4.57.6 + torch>=2.8 (see pyproject.toml — Colab's training
# environment, avoids the transformers 5.x LayerNorm key regression).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group ml

# Pre-cache the base architecture + tokenizer so first boot doesn't need
# network access to Hugging Face Hub. The state_dict is overridden at
# runtime by the fine-tuned weights pulled from MinIO; only the
# tokenizer config and base architecture come from this cache.
ENV HF_HOME=/app/.cache/huggingface
RUN /app/.venv/bin/python -c "from transformers import AutoTokenizer, DistilBertForSequenceClassification; \
    AutoTokenizer.from_pretrained('distilbert-base-uncased'); \
    DistilBertForSequenceClassification.from_pretrained('distilbert-base-uncased')"

# Application code — both the shared app/ tree (for app.infra.vault_client
# etc.) and the model_server/ package itself.
COPY app/ ./app/
COPY model_server/ ./model_server/

ENV PATH="/app/.venv/bin:$PATH"

# Inference container port; matches docker-compose's MODEL_SERVER_PORT mapping.
EXPOSE 8001

CMD ["uvicorn", "model_server.main:app", "--host", "0.0.0.0", "--port", "8001"]
