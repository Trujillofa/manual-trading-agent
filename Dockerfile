FROM python:3.11-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml ./
COPY src/ ./src/
COPY config/ ./config/
COPY scripts/run_scanner_loop.sh ./scripts/run_scanner_loop.sh
COPY .env.example ./

RUN chmod +x /app/scripts/run_scanner_loop.sh

# Install Python dependencies
RUN pip install --no-cache-dir .

# Create logs directory
RUN mkdir -p /app/logs

# Create data directory for SQLite candle persistence (bind-mounted to host ./data)
RUN mkdir -p /app/data

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose metrics port (for future Prometheus integration)
EXPOSE 9090

# Run the agent
CMD ["python", "-m", "src.cli", "scan"]

# --- Test stage (not included in production image) ---
FROM base AS test

COPY tests/ ./tests/
RUN pip install --no-cache-dir ".[dev]"

CMD ["pytest", "tests/", "-v", "--tb=short"]
