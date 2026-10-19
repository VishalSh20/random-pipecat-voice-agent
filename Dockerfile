# Pipecat Cloud agent image.
#
# Based on the official Pipecat Cloud base image, which provides the HTTP
# /bot entrypoint, the Daily/WebSocket transports and the system dependencies.
# The base image runs bot.py itself, so no CMD/ENTRYPOINT is required.
#
# Build for the architecture Daily-hosted regions run on:
#   docker build --platform linux/arm64 -t voice-agent-101 .

FROM dailyco/pipecat-base:latest-py3.13

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install dependencies from the lockfile. Project code is copied separately so
# this layer caches across code changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev --inexact

# Pipecat Cloud requires a bot.py exposing an async bot() entrypoint.
# Do not copy into /app; the base image reserves that path for itself.
COPY ./bot.py bot.py
COPY ./agent.py agent.py
COPY ./metrics.py metrics.py
