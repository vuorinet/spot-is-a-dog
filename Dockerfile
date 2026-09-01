# syntax=docker/dockerfile:1.7-labs
FROM ghcr.io/astral-sh/uv:python3.12-bookworm AS base

# Build argument for version (will be set from git SHA or provided value)
ARG SPOT_VERSION=unknown

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY spot ./spot
COPY templates ./templates
COPY static ./static
# --frozen: install exactly what uv.lock pins, never re-resolve against PyPI.
# Without this, `uv run` at container start (see CMD) re-resolves fresh on
# every restart, silently drifting to whatever newer dependency versions
# happen to be current on PyPI - which is how a Starlette major-version
# bump once broke every page in production without a single line of this
# repo's code changing.
RUN uv sync --frozen --no-dev
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV TZ=Europe/Helsinki

# Set version as environment variable
ENV SPOT_VERSION=${SPOT_VERSION}

EXPOSE 8000
CMD ["uv", "run", "--frozen", "uvicorn", "spot.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
