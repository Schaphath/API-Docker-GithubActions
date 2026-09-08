# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /build

COPY Api/requirements_api.txt .

RUN python -m venv "$VIRTUAL_ENV"

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip && \
    python -m pip install --no-warn-script-location -r requirements_api.txt

FROM python:3.12-slim AS runner

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN groupadd -g 10001 apigroup && \
    useradd --uid 10001 --gid apigroup --shell /usr/sbin/nologin --no-create-home apiuser

COPY --from=builder /opt/venv /opt/venv

COPY --chown=apiuser:apigroup ./Api /app/api

WORKDIR /app/api

USER apiuser

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

EXPOSE 8000

STOPSIGNAL SIGTERM

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]