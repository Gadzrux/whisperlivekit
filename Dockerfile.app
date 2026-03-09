FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY app ./app

RUN uv sync --frozen --no-dev

EXPOSE 8080

CMD ["uv", "run", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8080"]
