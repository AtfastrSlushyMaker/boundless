# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS dependencies

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /usr/local/bin/
ENV UV_PYTHON_DOWNLOADS=0 UV_LINK_MODE=copy
WORKDIR /app/backend
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM python:3.12-slim-bookworm
ENV PATH=/app/backend/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY --from=dependencies /app/backend/.venv ./.venv
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
RUN groupadd --gid 10001 boundless \
    && useradd --uid 10001 --gid boundless --home-dir /app --no-create-home boundless \
    && install -d -m 700 -o 10001 -g 10001 /app/.secrets /app/data/portraits
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=4).close()"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
