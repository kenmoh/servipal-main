# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim as builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy files to install dependencies
COPY pyproject.toml uv.lock ./

# Install dependencies
# --no-dev: Install only production dependencies
# --frozen: Sync exactly from uv.lock
# --no-install-project: We'll copy the project code later
RUN uv sync --frozen --no-dev --no-install-project

FROM python:${PYTHON_VERSION}-slim

WORKDIR /app

# Copy the environment from the builder
COPY --from=builder /app/.venv /app/.venv

# Enable the virtual environment
ENV PATH="/app/.venv/bin:$PATH"

# Copy the application code
COPY . .

# Create a non-privileged user
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/nonexistent" \
    --shell "/sbin/nologin" \
    --no-create-home \
    --uid "${UID}" \
    appuser

USER appuser

# Expose the port
ENV PORT=8080
EXPOSE 8080

# Run the application
# We use the shell form or strict exec form. 
# Cloud Run injects the PORT env var.
CMD ["sh", "-c", "fastapi run app/main.py --port ${PORT} --host 0.0.0.0"]
