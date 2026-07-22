FROM python:3-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY scale.py .
ENV PATH="/app/.venv/bin:$PATH"
RUN ln -s /app/scale.py /usr/local/bin/scale && chmod +x /app/scale.py
