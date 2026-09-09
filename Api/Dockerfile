# syntax=docker/dockerfile:1.7

# Etape d'installation des dépendances dans un environnement virtuel
FROM python:3.12-slim AS builder

# Déclare la variable d'environnement du VirtualEnv et l'ajoute en tête de la variable PATH.
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /build

COPY Api/requirements_api.txt .

# Crée un environnement virtuel Python isolé dans /opt/venv.
RUN python -m venv "$VIRTUAL_ENV"

# Installe les bibliothèques Python listées dans requirements_api.txt.
# --mount=type=cache réutilise les archives .whl téléchargées lors des builds précédents.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip && \
    python -m pip install --no-warn-script-location -r requirements_api.txt

# Etape de construction pour l'image finale
FROM python:3.12-slim AS runner

# Configuration des variables système pour Python
# PYTHONDONTWRITEBYTECODE=1 : empêche Python d'écrire des fichiers de cache inutiles dans le conteneur
# PYTHONUNBUFFERED=1 : force Python à envoyer les logs directement vers la sortie standard (stdout/stderr) en temps réel, sans mise en mémoire tampon
ENV PYTHONDONTWRITEBYTECODE=1 \ 
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Utilisateur non-root avec identifiants fixes 
# --shell /usr/sbin/nologin : empêche toute personne d'ouvrir une session interactive.
# --no-create-home : Évite la création inutile d'un dossier /home/apiuser.
RUN groupadd -g 10001 apigroup && \
    useradd --uid 10001 --gid apigroup --shell /usr/sbin/nologin --no-create-home apiuser

# Transfert du venv avec attribution des droits à l'utilisateur non-root
COPY --from=builder --chown=apiuser:apigroup /opt/venv /opt/venv

# Copie le code source de l'API en attribuant directement la propriété des fichiers à l'utilisateur apiuser
COPY --chown=apiuser:apigroup ./Api /app/api

WORKDIR /app/api

# Bascule l'utilisateur actif du conteneur de root vers apiuser(10001) 
USER 10001

# Teste l'état de santé de l'API toutes les 30 secondes en interrogeant la route /health
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

EXPOSE 8000

# Spécifie le signal d'arrêt (en douceur) envoyé au conteneur lors d'un docker stop
STOPSIGNAL SIGTERM

# Commande de démarrage par défaut du conteneur
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]