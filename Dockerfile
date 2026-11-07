# ==============================================================================
# Builder Stage: Install runtime dependencies into dedicated virtualenv
# ==============================================================================
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install pinned runtime dependencies
COPY backend/requirements.txt /build/requirements.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r /build/requirements.txt

# ==============================================================================
# Final Stage: Production image with pre-warmed embeddings and FAISS indexes
# ==============================================================================
FROM python:3.11-slim AS final

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/backend" \
    APP_ENV="production" \
    PORT=8000 \
    HOST="0.0.0.0"

# Install curl for container HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user and group
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/bash -m appuser

WORKDIR /app

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy application source and knowledge resources
COPY backend /app/backend
COPY knowledge /app/knowledge

# Set up writable directories for knowledge indexes, learned knowledge and logs
# Note: Knowledge indexes are built explicitly via one-off command:
# docker compose run --rm backend python -m app.knowledge.build_index --target all
RUN mkdir -p /app/knowledge/index /app/knowledge/learned /app/logs /tmp && \
    chown -R appuser:appgroup /app /tmp

USER appuser

EXPOSE 8000

# Docker healthcheck targeting /health liveness probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "65"]

