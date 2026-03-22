# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

# Set the working directory
WORKDIR /app

# Install system dependencies (including standard certs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (Fast python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:${PATH}"

# Copy the dependency files
COPY pyproject.toml .

# Install dependencies into the system python (since UV_SYSTEM_PYTHON=1)
RUN uv sync --no-dev

# Copy the rest of the application code
COPY . .

# Expose the Prometheus metrics port
EXPOSE 8000

# Command to run the application
CMD ["python", "main.py"]
