# syntax=docker/dockerfile:1.7-labs
FROM ghcr.io/astral-sh/uv:python3.12-bookworm AS base

# Build argument for version (will be set from git SHA or provided value)
ARG SPOT_VERSION=unknown

WORKDIR /app
COPY pyproject.toml ./
COPY spot ./spot
COPY templates ./templates
COPY static ./static
RUN uv pip install --system --no-cache .
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV TZ=Europe/Helsinki

# Set version as environment variable
ENV SPOT_VERSION=${SPOT_VERSION}

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "spot.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
