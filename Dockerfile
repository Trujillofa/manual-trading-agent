FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

# Copy source code
COPY src/ ./src/
COPY config/ ./config/
COPY tests/ ./tests/

# Create logs directory
RUN mkdir -p /app/logs

# Default command
ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["--help"]
