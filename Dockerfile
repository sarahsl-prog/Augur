# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

# Install Node.js for Phoenix MCP (used by Eval/Improvement agents in steps 7+).
# Even though steps 1-5 don't invoke MCP, baking Node in early avoids a
# multi-stage rebuild later.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv, then sync deps (better caching: deps before code)
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source last so dep layer stays cached on code changes
COPY src/ ./src/
COPY prompts/ ./prompts/

ENV PORT=8080
EXPOSE 8080

# Cloud Run requires binding to $PORT
CMD ["uv", "run", "--no-dev", "uvicorn", "augur.main:app", "--host", "0.0.0.0", "--port", "8080"]
