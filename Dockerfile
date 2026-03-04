# Setup with uv
# https://docs.astral.sh/uv/guides/integration/docker/
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /uvx /bin/
ENV UV_NO_DEV=1
ENV UV_NO_CACHE=1
ENV UV_PROJECT_ENVIRONMENT=/usr/local

# Install dependencies and activate environment
COPY pyproject.toml .
COPY uv.lock .
# Switch from local path to GitHub staging for Docker build
RUN sed -i 's|^viresclient = { path.*|# viresclient = { path = "../../VirES-Python-Client", editable = true }|' pyproject.toml && \
    sed -i 's|^# viresclient = { git.*|viresclient = { git = "https://github.com/ESA-VirES/VirES-Python-Client.git", rev = "staging" }|' pyproject.toml
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
