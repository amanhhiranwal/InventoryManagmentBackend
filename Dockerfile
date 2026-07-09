# ==============================================================================
# Phase 1: Foundational OS & Python Environment Selection
# ==============================================================================
FROM python:3.11-slim

# Enforce explicit performance and configuration parameters for Python in Docker
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Establish the isolated internal container workspace directory
WORKDIR /app

# Provision a highly secure, non-root application user to prevent container escaping
RUN useradd --uid 10001 --create-home appuser

# ==============================================================================
# Phase 2: System and Package Dependency Resolution
# ==============================================================================
# Isolate requirements.txt copying to maximize Docker's layer caching engine
COPY requirements.txt .

# Install compilation prerequisites, execute pip, and clean files in one layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && pip install --upgrade pip \
    && pip install -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

# ==============================================================================
# Phase 3: Application Code Ingestion & Permissions Assignment
# ==============================================================================
# Inject the cloned repository source files directly into the active layer
COPY . .

# Transfer filesystem permissions completely from root to your runtime user
RUN chown -R appuser:appuser /app

# Switch operational execution context to the low-privilege user account
USER appuser

# Declare the local networking boundary mapping intent
EXPOSE 8000

# ==============================================================================
# Phase 4: Production-Grade Process Execution
# ==============================================================================
# Invokes Uvicorn cleanly. The docker-compose workflow will dynamically
# override this command to inject '--reload' during your local development.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]