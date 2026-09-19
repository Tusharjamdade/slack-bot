FROM ghcr.io/astral-sh/uv:latest AS uv_bin
FROM python:3.12-slim

WORKDIR /app

# Set production environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE_PATH=/app/cache/fastembed

# Install curl for container healthcheck and basic build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy uv package manager binary
COPY --from=uv_bin /uv /uvx /bin/

# Copy dependency specifications first for optimal Docker layer caching
COPY pyproject.toml requirement.txt ./

# Install project dependencies with uv
RUN uv pip install --system --no-cache -r requirement.txt

# Pre-download FastEmbed model during build stage so ECS Fargate containers
# have zero runtime startup delay or external download dependencies
RUN mkdir -p /app/cache/fastembed && \
    python3 -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='/app/cache/fastembed')"

# Copy application source code and static UI assets
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY static/ ./static/
COPY main.py ./

# Create non-root user and set permissions for security in ECS Fargate
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Container healthcheck for ECS Target Group and Docker
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
