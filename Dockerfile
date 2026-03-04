# Setup with uv
# https://docs.astral.sh/uv/guides/integration/docker/
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /uvx /bin/
ENV UV_NO_DEV=1
ENV UV_NO_CACHE=1
ENV UV_PROJECT_ENVIRONMENT=/usr/local

# Install git for uv to clone from git sources
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Install dependencies and activate environment
COPY pyproject.toml .
COPY uv.lock .
RUN uv sync --frozen
ENV PATH="/app/.venv/bin:$PATH"

# Add project
WORKDIR /app
COPY src/ /app/src/

# Copy the entrypoint script and set it
# NB: container must be supplied with $VIRES_TOKEN at runtime (e.g. via .env file)
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
