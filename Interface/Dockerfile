# # syntax=docker/dockerfile:1.7

# Etape d'installation des dépendances dans un environnement virtuel
FROM python:3.12-slim AS builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# Isolation et installation des dépendances
COPY Interface/requirements_interface.txt .

RUN python -m venv "$VIRTUAL_ENV"

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip && \
    python -m pip install --no-warn-script-location -r requirements_interface.txt


# Etape de construction pour l'image finale
FROM python:3.12-slim AS runtime

# Variables système Python & Streamlit
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    STREAMLIT_SERVER_HEADLESS=true

WORKDIR /app

# Création du groupe et de l'utilisateur non-root
RUN groupadd -g 10001 interfacegroup && \
    useradd -u 10001 -g interfacegroup -s /sbin/nologin --no-create-home interfaceuser && \
    mkdir -p /app/.streamlit && \
    chown -R interfaceuser:interfacegroup /app

# Copie de l'environnement virtuel et du code applicatif
COPY --from=builder --chown=interfaceuser:interfacegroup /opt/venv /opt/venv
COPY --chown=interfaceuser:interfacegroup ./Interface /app

# Passage définitif sur l'utilisateur non-root
USER 10001

# Sonde de santé native Streamlit
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

EXPOSE 8501

STOPSIGNAL SIGTERM

CMD ["streamlit", "run", "app.py", "--server.port", "8501", "--server.address", "0.0.0.0"]