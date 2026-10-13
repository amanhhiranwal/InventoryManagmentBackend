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

# --proxy-headers so the app believes nginx about the scheme. Without
# it every redirect uvicorn builds says http, and a browser on the
# https site refuses to follow one - which is how a trailing slash on
# a Masters screen turned into "Network Error" in production and
# worked perfectly on a laptop.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' --workers $UVICORN_WORKERS"]