FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UVICORN_WORKERS=4

WORKDIR /app

RUN useradd --uid 10001 --create-home appuser

COPY requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && pip install --upgrade pip \
    && pip install -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000

# ==============================================================================
# Phase 4: Production-Grade Process Execution
# ==============================================================================
# Invokes Uvicorn cleanly. The docker-compose workflow will dynamically
# override this command to inject '--reload' during your local development.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]

#Testing Testing
