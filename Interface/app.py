"""Interface Streamlit pour OncoScan.

Outil de demonstration et d'aide a la decision. Il ne remplace jamais
l'evaluation d'un professionnel de sante.
"""

from __future__ import annotations

import logging
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import bcrypt
import pandas as pd
import psycopg2
import requests
import streamlit as st
from psycopg2 import pool


APP_VERSION = "1.4.0"
MAX_HISTORY_ROWS = 50
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
MIN_PASSWORD_LENGTH = 8
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{3,30}$")

FEATURES = [
    "texture_worst",
    "area_worst",
    "smoothness_worst",
    "compactness_worst",
    "concavity_worst",
    "concave_points_worst",
    "symmetry_worst",
    "fractal_dimension_worst",
]

FEATURE_BOUNDS = {
    "texture_worst": (10.0, 50.0, 13.3, 0.1),
    "area_worst": (150.0, 4300.0, 850.0, 10.0),
    "smoothness_worst": (0.05, 0.25, 0.14, 0.001),
    "compactness_worst": (0.02, 1.10, 0.28, 0.001),
    "concavity_worst": (0.01, 1.30, 0.32, 0.001),
    "concave_points_worst": (0.02, 0.40, 0.15, 0.001),
    "symmetry_worst": (0.10, 0.70, 0.29, 0.001),
    "fractal_dimension_worst": (0.04, 0.22, 0.08, 0.001),
}

FEATURE_HELP = {
    "texture_worst": "Variation du niveau de gris dans la zone la plus irreguliere.",
    "area_worst": "Aire de la plus grande section observee de la tumeur.",
    "smoothness_worst": "Variation locale de la longueur du contour tumoral.",
    "compactness_worst": "Ecart de la forme par rapport a un cercle parfait.",
    "concavity_worst": "Severite des portions concaves du contour.",
    "concave_points_worst": "Nombre de portions concaves du contour.",
    "symmetry_worst": "Degre d'asymetrie de la forme tumorale.",
    "fractal_dimension_worst": "Complexite et irregularite du contour.",
}


@dataclass(frozen=True)
class Settings:
    api_url: str
    api_key: str | None
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str


@st.cache_resource
def get_settings() -> Settings:
    return Settings(
        api_url=os.getenv("API_URL", "http://api:8000/predict"),
        api_key=os.getenv("API_KEY") or None,
        db_host=os.getenv("DB_HOST", "postgres"),
        db_port=int(os.getenv("DB_PORT", "5432")),
        db_name=os.getenv("DB_NAME", "oncoscan"),
        db_user=os.getenv("DB_USER", "oncoscan"),
        db_password=os.getenv("DB_PASSWORD", ""),
    )


@st.cache_resource
def get_connection_pool(settings: Settings) -> pool.ThreadedConnectionPool:
    if not settings.db_password:
        raise RuntimeError("DB_PASSWORD n'est pas configuree.")
    return pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=10,
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        connect_timeout=5,
    )


@contextmanager
def db_cursor(settings: Settings) -> Iterator[Any]:
    """Garantit commit, rollback et retour de la connexion au pool."""
    connection = get_connection_pool(settings).getconn()
    try:
        with connection.cursor() as cursor:
            yield cursor
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        get_connection_pool(settings).putconn(connection)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def valid_username(username: str) -> bool:
    return bool(USERNAME_PATTERN.fullmatch(username))


def valid_password(password: str) -> bool:
    return (
        len(password) >= MIN_PASSWORD_LENGTH
        and any(character.isalpha() for character in password)
        and any(character.isdigit() for character in password)
    )


def initialise_session() -> None:
    defaults = {
        "authenticated": False,
        "username": None,
        "user_id": None,
        "failed_attempts": 0,
        "locked_until": 0.0,
        "last_prediction": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def login(settings: Settings, username: str, password: str) -> tuple[bool, str]:
    if time.time() < st.session_state.locked_until:
        remaining = max(1, int(st.session_state.locked_until - time.time()))
        return False, f"Trop de tentatives. Reessayez dans {remaining} seconde(s)."
    try:
        with db_cursor(settings) as cursor:
            cursor.execute(
                "SELECT id, password_hash FROM users WHERE username = %s",
                (username,),
            )
            row = cursor.fetchone()
    except (psycopg2.Error, RuntimeError):
        logging.getLogger(__name__).exception("Echec de lecture de l'utilisateur")
        return False, "Le service de comptes est temporairement indisponible."

    if row and verify_password(password, row[1]):
        st.session_state.authenticated = True
        st.session_state.username = username
        st.session_state.user_id = row[0]
        st.session_state.failed_attempts = 0
        st.session_state.locked_until = 0.0
        return True, "Connexion reussie."

    st.session_state.failed_attempts += 1
    if st.session_state.failed_attempts >= MAX_LOGIN_ATTEMPTS:
        st.session_state.locked_until = time.time() + LOCKOUT_SECONDS
        st.session_state.failed_attempts = 0
    return False, "Identifiant ou mot de passe incorrect."


def register_user(settings: Settings, username: str, password: str) -> tuple[bool, str]:
    try:
        with db_cursor(settings) as cursor:
            cursor.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, hash_password(password)),
            )
        return True, "Compte cree. Vous pouvez maintenant vous connecter."
    except psycopg2.errors.UniqueViolation:
        return False, "Cet identifiant existe deja."
    except (psycopg2.Error, RuntimeError):
        logging.getLogger(__name__).exception("Echec de creation du compte")
        return False, "Le service de comptes est temporairement indisponible."


def request_prediction(settings: Settings, inputs: dict[str, float]) -> dict[str, Any]:
    headers = {"X-API-Key": settings.api_key} if settings.api_key else {}
    response = requests.post(
        settings.api_url,
        json=inputs,
        headers=headers,
        timeout=(3, 15),
    )
    if response.status_code != 200:
        if response.status_code in (401, 403):
            raise RuntimeError("Le service de prediction a refuse la requete.")
        if response.status_code == 422:
            raise ValueError("Les valeurs saisies sont hors des limites acceptees.")
        if response.status_code == 503:
            raise RuntimeError("Le modele de prediction est indisponible.")
        raise RuntimeError(f"Le service de prediction a repondu {response.status_code}.")
    return response.json()


def save_prediction(settings: Settings, inputs: dict[str, float], result: dict[str, Any]) -> None:
    probability = result.get("probability")
    probability_pct = round(float(probability) * 100, 2) if probability is not None else None
    values = [inputs[feature] for feature in FEATURES]
    with db_cursor(settings) as cursor:
        cursor.execute(
            """
            INSERT INTO predictions (
                user_id, texture_worst, area_worst, smoothness_worst,
                compactness_worst, concavity_worst, concave_points_worst,
                symmetry_worst, fractal_dimension_worst, prediction, probability_pct
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (st.session_state.user_id, *values, result["prediction"], probability_pct),
        )


def get_history(settings: Settings) -> pd.DataFrame:
    with db_cursor(settings) as cursor:
        cursor.execute(
            """
            SELECT created_at, prediction, probability_pct,
                   texture_worst, area_worst, smoothness_worst,
                   compactness_worst, concavity_worst, concave_points_worst,
                   symmetry_worst, fractal_dimension_worst
            FROM predictions
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (st.session_state.user_id, MAX_HISTORY_ROWS),
        )
        rows = cursor.fetchall()
    columns = [
        "Date et heure", "Diagnostic", "Probabilite (%)",
        *[feature.replace("_", " ").title() for feature in FEATURES],
    ]
    return pd.DataFrame(rows, columns=columns)


def render_prediction(result: dict[str, Any]) -> None:
    prediction = result.get("prediction")
    probability = result.get("probability")
    probability_text = f"{probability * 100:.2f} %" if probability is not None else "non disponible"
    if prediction == "M":
        st.error("Resultat du modele : classe maligne")
        st.warning(f"Probabilite estimee : {probability_text}. Ce resultat doit etre confirme par un professionnel de sante.")
    else:
        st.success("Resultat du modele : classe benigne")
        st.info(f"Probabilite estimee : {probability_text}.")
    st.caption(f"Version du modele : {result.get('model_version', 'inconnue')}")


def render_login(settings: Settings) -> None:
    _, content_column, _ = st.columns([1, 2.2, 1])
    with content_column:
        st.markdown(
            '<div class="auth-brand"><span class="brand-mark">O</span><span>OncoScan AI</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="auth-heading">'
            '<p class="auth-kicker">ESPACE PROFESSIONNEL</p>'
            '<h2 class="auth-title">Bienvenue dans votre espace</h2>'
            '<p class="auth-subtitle">Analysez les mesures cellulaires et retrouvez vos dossiers en un seul endroit.</p>'
            '</div>',
            unsafe_allow_html=True,
        )
        login_tab, register_tab = st.tabs(["Connexion", "Creer un compte"])
        with login_tab:
            with st.form("login_form"):
                username = st.text_input("Identifiant", placeholder="Votre identifiant")
                password = st.text_input("Mot de passe", type="password", placeholder="Votre mot de passe")
                submitted = st.form_submit_button("Se connecter", type="primary", use_container_width=False)
            if submitted:
                if not username.strip() or not password:
                    st.warning("Saisissez votre identifiant et votre mot de passe.")
                else:
                    success, message = login(settings, username.strip().lower(), password)
                    (st.success if success else st.error)(message)
                    if success:
                        st.rerun()
        with register_tab:
            with st.form("register_form"):
                username = st.text_input("Nouvel identifiant", placeholder="ex. dr.martin")
                password = st.text_input("Mot de passe", type="password", placeholder="8 caracteres minimum")
                confirmation = st.text_input("Confirmer le mot de passe", type="password")
                st.caption("3 à 30 caractères. Le mot de passe doit contenir une lettre et un chiffre.")
                submitted = st.form_submit_button("Creer le compte", use_container_width=False)
            if submitted:
                username = username.strip().lower()
                if not valid_username(username):
                    st.warning("Identifiant ou mot de passe invalide.")
                elif password != confirmation:
                    st.warning("Les mots de passe ne correspondent pas.")
                elif not valid_password(password):
                    st.warning("Le mot de passe doit contenir une lettre, un chiffre et au moins 8 caracteres.")
                else:
                    success, message = register_user(settings, username, password)
                    (st.success if success else st.error)(message)


def render_analysis(settings: Settings) -> None:
    st.subheader("Nouvelle analyse")
    st.caption("Saisissez les mesures disponibles dans le dossier médical.")
    with st.form("prediction_form"):
        inputs: dict[str, float] = {}
        columns = st.columns(2)
        for index, feature in enumerate(FEATURES):
            minimum, maximum, default, step = FEATURE_BOUNDS[feature]
            with columns[index % 2]:
                inputs[feature] = st.number_input(
                    feature.replace("_", " ").title(),
                    min_value=minimum,
                    max_value=maximum,
                    value=default,
                    step=step,
                    format="%.3f",
                    help=FEATURE_HELP[feature],
                )
        submitted = st.form_submit_button("Lancer l'analyse", type="primary", use_container_width=True)
    if submitted:
        with st.spinner("Analyse en cours..."):
            try:
                result = request_prediction(settings, inputs)
                st.session_state.last_prediction = result
                try:
                    save_prediction(settings, inputs, result)
                except (psycopg2.Error, RuntimeError):
                    logging.getLogger(__name__).exception("Echec de sauvegarde de l'analyse")
                    st.warning("Analyse terminee, mais l'historique n'a pas pu etre sauvegarde.")
            except requests.exceptions.Timeout:
                st.error("Le service de prediction met trop de temps a repondre.")
            except requests.exceptions.RequestException:
                logging.getLogger(__name__).exception("Echec de communication avec l'API")
                st.error("Impossible de contacter le service de prediction.")
            except (RuntimeError, ValueError) as error:
                st.error(str(error))
    if st.session_state.last_prediction:
        render_prediction(st.session_state.last_prediction)


def render_history(settings: Settings) -> None:
    st.subheader("Historique des analyses")
    try:
        history = get_history(settings)
    except (psycopg2.Error, RuntimeError):
        logging.getLogger(__name__).exception("Echec de lecture de l'historique")
        st.error("Impossible de charger l'historique pour le moment.")
        return
    if history.empty:
        st.info("Aucune analyse enregistree pour le moment.")
        return
    history["Diagnostic"] = history["Diagnostic"].map({"M": "Maligne", "B": "Benigne"})
    choice = st.selectbox("Filtrer", ["Toutes", "Maligne", "Benigne"], key="history_filter")
    if choice != "Toutes":
        history = history[history["Diagnostic"] == choice]
    show_details = st.checkbox("Afficher les mesures cliniques", key="history_details")
    visible_columns = ["Date et heure", "Diagnostic", "Probabilite (%)"]
    if show_details:
        visible_columns += [column for column in history.columns if column not in visible_columns]
    visible_history = history[visible_columns].copy()
    visible_history["Date et heure"] = pd.to_datetime(
        visible_history["Date et heure"], utc=True
    ).dt.strftime("%d/%m/%Y %H:%M")
    visible_history["Probabilite (%)"] = visible_history["Probabilite (%)"].map(
        lambda value: f"{value:.2f} %" if pd.notna(value) else "-"
    )
    table = visible_history.style.set_table_styles(
        [
            {
                "selector": "th",
                "props": [
                    ("background-color", "#f8eef1"),
                    ("color", "#8f4b60"),
                    ("font-weight", "600"),
                    ("border-bottom", "1px solid #ead6dc"),
                ],
            },
            {
                "selector": "td",
                "props": [
                    ("background-color", "#fffafb"),
                    ("color", "#30242a"),
                    ("border-bottom", "1px solid #f1e3e7"),
                ],
            },
        ]
    ).set_properties(
        **{"text-align": "left", "padding": "0.55rem", "font-size": "0.82rem"}
    )
    st.table(table)
    st.download_button(
        "Telecharger l'historique (CSV)",
        data=visible_history.to_csv(index=False).encode("utf-8-sig"),
        file_name="oncoscan_historique.csv",
        mime="text/csv",
        use_container_width=False,
    )


def render_dashboard(settings: Settings) -> None:
    with st.sidebar:
        st.markdown(
            '<div class="app-brand"><span class="app-brand-mark">O</span><span>OncoScan</span></div>',
            unsafe_allow_html=True,
        )
        st.caption(f"Session : {st.session_state.username}")
        st.divider()
        if st.button("Se deconnecter", use_container_width=True):
            for key in ("authenticated", "username", "user_id", "last_prediction"):
                st.session_state.pop(key, None)
            st.rerun()
        st.caption("Les resultats sont indicatifs et ne remplacent pas un avis médical.")
    st.markdown(
        '<div class="dashboard-heading">'
        '<p class="auth-kicker">ESPACE PROFESSIONNEL</p>'
        '<h1 class="dashboard-title">Votre espace clinique</h1>'
        '</div>',
        unsafe_allow_html=True,
    )
    analysis_tab, history_tab = st.tabs(["Nouvelle analyse", "Historique"])
    with analysis_tab:
        render_analysis(settings)
    with history_tab:
        render_history(settings)


def main() -> None:
    logo_path = Path(__file__).resolve().parent / "favicon.svg"
    st.set_page_config(
        page_title="OncoScan",
        page_icon=str(logo_path) if logo_path.is_file() else "O",
        layout="wide",
    )
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@600;700;800&display=swap');

        :root {
            --rose-50: #fffafb;
            --rose-100: #f8eef1;
            --rose-200: #ead6dc;
            --rose-400: #c98596;
            --rose-600: #9e5367;
            --ink: #30242a;
            --muted: #76656b;
            --white: #ffffff;
        }

        html, body, [class*="css"], .stApp,
        [data-testid="stAppViewContainer"], [data-testid="stMain"],
        [data-testid="stHeader"] {
            color: var(--ink) !important;
            background: var(--rose-50) !important;
            font-family: 'DM Sans', sans-serif !important;
        }

        [data-testid="stMainBlockContainer"] {
            max-width: 1120px !important;
            padding: 3.5rem 2rem 2rem !important;
        }

        h1, h2, h3, h4 { color: var(--ink) !important; font-family: 'Manrope', sans-serif !important; }
        p, label, [data-testid="stMarkdownContainer"] { color: var(--ink) !important; }
        [data-testid="stCaptionContainer"] p { color: var(--muted) !important; }

        .auth-brand { display: flex; align-items: center; justify-content: center; gap: 1rem; color: var(--ink); font: 800 2.5rem 'Manrope', sans-serif; margin: 0 0 3.25rem; }
        .brand-mark { display: grid; place-items: center; width: 4.3rem; height: 4.3rem; border-radius: 16px; color: var(--white); background: var(--rose-600); font-size: 2.35rem; box-shadow: 0 8px 18px rgba(158,83,103,.16); }
        .app-brand { display: flex; align-items: center; gap: .7rem; color: var(--ink); font: 800 1.45rem 'Manrope', sans-serif; margin: .35rem 0 1.4rem; }
        .app-brand-mark { display: grid; place-items: center; width: 2.65rem; height: 2.65rem; border-radius: 11px; color: var(--white); background: var(--rose-600); font-size: 1.45rem; box-shadow: 0 5px 12px rgba(158,83,103,.14); }
        .auth-heading { text-align: center; }
        .dashboard-heading { text-align: center; margin: 0 0 2rem; }
        .dashboard-title { font-size: clamp(1.7rem, 3vw, 2.45rem) !important; line-height: 1.15; margin: 0 0 .65rem; }
        .dashboard-heading .auth-subtitle { display: block; width: 100%; text-align: center; }
        .auth-kicker { color: var(--rose-600) !important; font-size: .72rem; font-weight: 700; letter-spacing: .14em; margin: 0 0 .8rem; }
        .auth-title { font-size: clamp(1.65rem, 3vw, 2.35rem) !important; line-height: 1.15; margin: 0 0 .7rem; }
        .auth-subtitle { color: var(--muted) !important; font-size: .98rem; line-height: 1.6; max-width: 44rem; margin: 0 auto 2rem; }
        .app-footer { width: 100%; padding: 1rem 0 0; text-align: center; color: var(--muted); font-size: .78rem; }
        [data-testid="stForm"] { width: 100%; max-width: 700px; margin: 0 auto; }

        [data-testid="stTabs"] [role="tablist"] { display: flex; gap: 0; border-bottom: 1px solid var(--rose-200); }
        [data-testid="stTabs"] button[role="tab"] { flex: 1 1 50%; justify-content: center; color: var(--muted) !important; font-weight: 600; padding: .75rem .15rem; }
        [data-testid="stTabs"] button[aria-selected="true"] { color: var(--rose-600) !important; border-bottom-color: var(--rose-600) !important; }

        [data-testid="stTextInput"] label, [data-testid="stNumberInput"] label { color: var(--ink) !important; font-size: .82rem; font-weight: 600; }
        [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input {
            color: var(--ink) !important;
            -webkit-text-fill-color: var(--ink) !important;
            background: var(--white) !important;
            border: 1px solid var(--rose-200) !important;
            border-radius: 8px !important;
            min-height: 2.7rem;
        }
        [data-testid="stTextInput"] [data-baseweb="input"], [data-testid="stNumberInput"] [data-baseweb="input"],
        [data-testid="stTextInput"] [data-baseweb="base-input"], [data-testid="stNumberInput"] [data-baseweb="base-input"] {
            background: var(--white) !important;
            border: 2px solid var(--rose-400) !important;
            border-radius: 9px !important;
            min-height: 2.7rem;
            overflow: hidden;
        }
        [data-testid="stTextInput"] [data-baseweb="input"] input, [data-testid="stNumberInput"] [data-baseweb="input"] input {
            color: var(--ink) !important;
            -webkit-text-fill-color: var(--ink) !important;
            background: transparent !important;
            border: 0 !important;
            outline: 0 !important;
            box-shadow: none !important;
        }
        [data-testid="stTextInput"] [data-baseweb="input"]:focus-within,
        [data-testid="stNumberInput"] [data-baseweb="input"]:focus-within {
            border-color: var(--rose-600) !important;
            box-shadow: 0 0 0 3px rgba(158,83,103,.16) !important;
        }
        [data-testid="stTextInput"]:has(input[type="password"]) [data-baseweb="input"] {
            min-height: 2.7rem;
            border: 2px solid var(--rose-400) !important;
            border-radius: 9px !important;
            overflow: hidden;
            display: flex !important;
            align-items: stretch !important;
            background: var(--white) !important;
        }
        [data-testid="stTextInput"]:has(input[type="password"]) [data-baseweb="input"] input {
            height: 2.55rem !important;
            min-height: 2.55rem !important;
            flex: 1 1 auto !important;
            border: 0 !important;
            outline: 0 !important;
            box-shadow: none !important;
        }
        [data-testid="stTextInput"]:has(input[type="password"]) [data-baseweb="input"] button {
            width: 2.8rem !important;
            min-width: 2.8rem !important;
            height: 2.55rem !important;
            margin: 0 !important;
            padding: 0 !important;
            border: 0 !important;
            border-left: 1px solid var(--rose-200) !important;
            border-radius: 0 !important;
            background: var(--rose-100) !important;
            color: var(--rose-600) !important;
            display: grid !important;
            place-items: center !important;
        }
        [data-testid="stTextInput"]:has(input[type="password"]) [data-baseweb="input"] button:hover {
            background: var(--rose-200) !important;
            color: var(--rose-600) !important;
        }
        [data-testid="stTextInput"]:has(input[type="password"]) [data-baseweb="input"] button svg {
            width: 1.15rem !important;
            height: 1.15rem !important;
            fill: currentColor !important;
            color: currentColor !important;
        }
        [data-testid="stTextInput"] div, [data-testid="stNumberInput"] div { background-color: var(--white) !important; }
        [data-testid="stTextInput"]:has(input[type="password"]) [data-baseweb="input"] button,
        [data-testid="stTextInput"]:has(input[type="password"]) [data-baseweb="input"] button > * {
            background: var(--rose-100) !important;
        }
        [data-testid="stTextInput"] input:-webkit-autofill,
        [data-testid="stTextInput"] input:-webkit-autofill:hover,
        [data-testid="stTextInput"] input:-webkit-autofill:focus,
        [data-testid="stTextInput"] input:-webkit-autofill:active {
            -webkit-text-fill-color: var(--ink) !important;
            -webkit-box-shadow: 0 0 0 1000px var(--white) inset !important;
            box-shadow: 0 0 0 1000px var(--white) inset !important;
            background-color: var(--white) !important;
            transition: background-color 9999s ease-in-out 0s;
        }
        [data-testid="stTextInput"] input::placeholder { color: #aa999f !important; opacity: 1 !important; }
        [data-testid="stTextInput"] input:focus, [data-testid="stNumberInput"] input:focus { border-color: var(--rose-600) !important; box-shadow: 0 0 0 3px rgba(158,83,103,.16) !important; }
        [data-testid="stTextInput"]:has(input[type="password"]) [data-baseweb="input"]:focus-within { border-color: var(--rose-600) !important; box-shadow: 0 0 0 3px rgba(158,83,103,.16) !important; }

        .stButton > button, [data-testid="stFormSubmitButton"] button {
            width: auto !important;
            min-width: 10.5rem;
            min-height: 2.65rem;
            padding: .55rem 1.25rem !important;
            border-radius: 8px !important;
            border: 1px solid var(--rose-600) !important;
            background: var(--rose-600) !important;
            color: var(--white) !important;
            font-weight: 700 !important;
            box-shadow: 0 5px 12px rgba(158,83,103,.14) !important;
        }
        [data-testid="stFormSubmitButton"] { display: flex; justify-content: center; }
        .stButton > button:hover, [data-testid="stFormSubmitButton"] button:hover { background: #874459 !important; border-color: #874459 !important; }

        [data-testid="stForm"] { padding-top: 1.4rem; }
        [data-testid="stSidebar"] { background: var(--rose-100) !important; border-right: 1px solid var(--rose-200); }
        [data-testid="stSidebar"] .stButton > button { width: 100% !important; background: var(--white) !important; color: var(--rose-600) !important; border-color: var(--rose-400) !important; box-shadow: none !important; transition: background .18s ease, color .18s ease, border-color .18s ease, transform .18s ease !important; }
        [data-testid="stSidebar"] .stButton > button:hover { background: var(--rose-600) !important; color: var(--white) !important; border-color: var(--rose-600) !important; transform: translateY(-1px); box-shadow: 0 5px 12px rgba(158,83,103,.16) !important; }
        [data-testid="stSidebar"] .stButton > button:active { transform: translateY(0); }
        [data-testid="stSelectbox"] label { color: var(--ink) !important; font-size: .82rem; font-weight: 600; }
        [data-testid="stSelectbox"] [data-baseweb="select"] > div { min-height: 2.45rem; background: var(--white) !important; color: var(--ink) !important; border: 1px solid var(--rose-200) !important; border-radius: 8px !important; box-shadow: none !important; }
        [data-testid="stSelectbox"] [data-baseweb="select"] span { color: var(--ink) !important; }
        [data-testid="stSelectbox"] [data-baseweb="select"] svg { fill: var(--rose-600) !important; }
        [data-baseweb="popover"], [data-baseweb="menu"] { background: var(--white) !important; border: 1px solid var(--rose-200) !important; border-radius: 8px !important; box-shadow: 0 8px 24px rgba(158,83,103,.14) !important; }
        [data-baseweb="menu"] [role="option"] { background: var(--white) !important; color: var(--ink) !important; }
        [data-baseweb="menu"] [role="option"]:hover, [data-baseweb="menu"] [aria-selected="true"] { background: var(--rose-100) !important; color: var(--rose-600) !important; }
        [data-testid="stCheckbox"] label { color: var(--ink) !important; }
        [data-testid="stCheckbox"] [data-baseweb="checkbox"] { background: var(--white) !important; border: 1px solid var(--rose-400) !important; border-radius: 5px !important; }
        [data-testid="stCheckbox"] [data-baseweb="checkbox"][aria-checked="true"] { background: var(--rose-600) !important; border-color: var(--rose-600) !important; }
        [data-testid="stCheckbox"] [data-baseweb="checkbox"] svg { fill: var(--white) !important; color: var(--white) !important; }
        [data-testid="stDownloadButton"] { display: flex !important; justify-content: center !important; }
        [data-testid="stDownloadButton"] button { width: auto !important; min-width: 0 !important; background: var(--white) !important; color: var(--rose-600) !important; border: 1px solid var(--rose-400) !important; box-shadow: none !important; transition: background .18s ease, color .18s ease, border-color .18s ease, transform .18s ease !important; }
        [data-testid="stDownloadButton"] button:hover { background: var(--rose-600) !important; color: var(--white) !important; border-color: var(--rose-600) !important; transform: translateY(-1px); box-shadow: 0 5px 12px rgba(158,83,103,.16) !important; }
        [data-testid="stDownloadButton"] button:active { transform: translateY(0); }
        [data-testid="stTable"] table { width: 100%; background: var(--white) !important; border: 1px solid var(--rose-200); border-collapse: separate; border-spacing: 0; border-radius: 8px; overflow: hidden; }
        [data-testid="stTable"] th { background: var(--rose-100) !important; color: var(--rose-600) !important; font-size: .76rem !important; font-weight: 700 !important; border-bottom: 1px solid var(--rose-200) !important; }
        [data-testid="stTable"] td { background: var(--white) !important; color: var(--ink) !important; font-size: .82rem !important; border-bottom: 1px solid #f1e3e7 !important; }
        [data-testid="stTable"] tr:last-child td { border-bottom: 0 !important; }
        [data-testid="stAlert"] { border-radius: 8px !important; }
        [data-testid="stDataFrame"] { border: 1px solid var(--rose-200); border-radius: 8px; overflow: hidden; background: var(--white) !important; }
        [data-testid="stDataFrame"] [role="columnheader"] { background: var(--rose-100) !important; color: var(--rose-600) !important; }
        [data-testid="stDataFrame"] [role="gridcell"] { background: var(--white) !important; color: var(--ink) !important; }
        @media (max-width: 640px) { [data-testid="stMainBlockContainer"] { padding: 2rem 1rem !important; } .auth-brand { margin-bottom: 2rem; font-size: 2rem; } .brand-mark { width: 3.4rem; height: 3.4rem; font-size: 1.8rem; } }
        </style>
        """,
        unsafe_allow_html=True,
    )
    initialise_session()
    settings = get_settings()
    if st.session_state.authenticated:
        render_dashboard(settings)
    else:
        render_login(settings)
    st.divider()
    st.markdown(
        f'<div class="app-footer">OncoScan AI v{APP_VERSION} | '
        "Outil d'aide à la décision médicale | by @Madiba.</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
