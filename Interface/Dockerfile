# ÉTAPE 1 : BUILDER
FROM python:3.12-slim AS builder

WORKDIR /build 

# Copie et installation des dépendances propres à l'interface
COPY Interface/requirements_interface.txt .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install --prefix=/install --no-warn-script-location -r requirements_interface.txt


# ÉTAPE 2 : RUNTIME
FROM python:3.12-slim AS runtime

# Variables d'environnement Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Création de l'utilisateur non-root sécurisé
RUN groupadd -g 10001 interfacegroup && \
    useradd -u 10001 -g interfacegroup -s /sbin/nologin interfaceuser

# Copie des dépendances installées
COPY --from=builder /install /usr/local

# Copie directe du contenu d'Interface dans /app
COPY --chown=interfaceuser:interfacegroup ./Interface /app

# Passage sur l'utilisateur non-root
USER 10001

# Healthcheck natif Streamlit
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port", "8501", "--server.address", "0.0.0.0"]