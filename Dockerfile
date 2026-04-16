# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Set the working directory
WORKDIR /app

# Install system dependencies (including standard certs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (Fast python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
ENV PATH="/root/.cargo/bin:${PATH}"

# Copy the dependency files first (layer caching)
COPY pyproject.toml uv.lock ./

# Install dependencies via uv sync (creates .venv)
RUN uv sync --no-dev --frozen
ENV PATH="/app/.venv/bin:$PATH"

# Copy the rest of the application code
COPY . .

# Non-root user for security
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

# Expose the Prometheus metrics port
EXPOSE 8000

# Command to run the application
CMD ["python", "main.py"]
