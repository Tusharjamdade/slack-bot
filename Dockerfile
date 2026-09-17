FROM ghcr.io/astral-sh/uv:latest AS uv_bin
FROM python:3.12-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy uv package manager binary
COPY --from=uv_bin /uv /uvx /bin/

# Copy dependency specifications first for Docker layer caching
COPY pyproject.toml requirement.txt ./

# Install project dependencies with uv
RUN uv pip install --system --no-cache -r requirement.txt

# Copy application source code
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY main.py ./

# Run as non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
