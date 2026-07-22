FROM python:3-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY scale.py .
RUN uv sync --frozen --no-dev && ln -s /app/.venv/bin/scale /usr/local/bin/scale
