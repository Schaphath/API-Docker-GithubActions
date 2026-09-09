"""API REST de prediction du modele de cancer du sein."""

import logging
import os
import pickle
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = Path(
    os.getenv("MODEL_PATH", BASE_DIR.parent / "Save_models" / "decision_tree_best.pkl")
)
SCALER_PATH = Path(
    os.getenv("SCALER_PATH", BASE_DIR.parent / "Save_models" / "MinMax_scaler.pkl")
)
API_KEY = os.getenv("API_KEY")
MODEL_VERSION = "1.2.0"

FEATURE_ORDER = [
    "texture_worst",
    "area_worst",
    "smoothness_worst",
    "compactness_worst",
    "concavity_worst",
    "concave_points_worst",
    "symmetry_worst",
    "fractal_dimension_worst",
]


class PredictionRequest(BaseModel):
    """Valeurs attendues par le modele."""

    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        json_schema_extra={
            "examples": [
                {
                    "texture_worst": 25.3,
                    "area_worst": 850.0,
                    "smoothness_worst": 0.14,
                    "compactness_worst": 0.28,
                    "concavity_worst": 0.32,
                    "concave_points_worst": 0.15,
                    "symmetry_worst": 0.29,
                    "fractal_dimension_worst": 0.08,
                }
            ]
        },
    )

    texture_worst: float = Field(gt=10.0, le=50.0)
    area_worst: float = Field(gt=150.0, le=4300.0)
    smoothness_worst: float = Field(gt=0.05, le=0.25)
    compactness_worst: float = Field(ge=0.02, le=1.10)
    concavity_worst: float = Field(ge=0.01, le=1.30)
    concave_points_worst: float = Field(ge=0.02, le=0.40)
    symmetry_worst: float = Field(gt=0.10, le=0.70)
    fractal_dimension_worst: float = Field(gt=0.04, le=0.22)


class PredictionResponse(BaseModel):
    prediction: Literal["M", "B"]
    label: Literal["Maligne", "Bénigne"]
    probability: float | None = Field(default=None, ge=0.0, le=1.0)
    model_version: str = MODEL_VERSION


class HealthResponse(BaseModel):
    status: Literal["healthy", "unhealthy"]
    model_loaded: bool
    scaler_loaded: bool
    model_version: str = MODEL_VERSION


def load_pickle(path: Path) -> Any:
    """Charge un artefact local deja controle par le proprietaire du projet."""
    if not path.is_file():
        raise RuntimeError(f"Artefact introuvable: {path}")

    try:
        with path.open("rb") as file:
            return pickle.load(file)
    except Exception as error:
        raise RuntimeError(f"Impossible de charger {path.name}") from error


def validate_model_contract(model: Any, scaler: Any) -> list[str]:
    """Verifie au demarrage que les artefacts correspondent a l'API."""
    scaler_features = getattr(scaler, "feature_names_in_", None)
    if scaler_features is not None and set(scaler_features) != set(FEATURE_ORDER):
        raise RuntimeError(
            "Les variables du scaler ne correspondent pas a celles de l'API."
        )

    classes = list(getattr(model, "classes_", []))
    if 1 not in classes and "M" not in classes:
        raise RuntimeError(
            f"Classe maligne introuvable dans le modele: {classes}"
        )
    return list(scaler_features) if scaler_features is not None else FEATURE_ORDER


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Charge les artefacts une fois avant d'accepter des requetes."""
    model = load_pickle(MODEL_PATH)
    scaler = load_pickle(SCALER_PATH)
    app.state.feature_order = validate_model_contract(model, scaler)

    app.state.model = model
    app.state.scaler = scaler
    logger.info("Modele et scaler charges")
    yield
    app.state.model = None
    app.state.scaler = None


app = FastAPI(
    title="OncoScan API",
    description="API de prediction du cancer du sein",
    version=MODEL_VERSION,
    lifespan=lifespan,
)

allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-Key", "Content-Type"],
    )


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Authentifie la requete si une cle est configuree."""
    if API_KEY and (not x_api_key or not secrets.compare_digest(x_api_key, API_KEY)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cle API invalide ou manquante",
        )


@app.get("/", tags=["Info"])
def root() -> dict[str, str]:
    return {"name": "OncoScan API", "version": MODEL_VERSION}


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health(request: Request) -> HealthResponse:
    model_loaded = getattr(request.app.state, "model", None) is not None
    scaler_loaded = getattr(request.app.state, "scaler", None) is not None
    return HealthResponse(
        status="healthy" if model_loaded and scaler_loaded else "unhealthy",
        model_loaded=model_loaded,
        scaler_loaded=scaler_loaded,
    )


@app.post(
    "/predict",
    response_model=PredictionResponse,
    status_code=status.HTTP_200_OK,
    tags=["Prediction"],
    dependencies=[Depends(require_api_key)],
)
def predict(data: PredictionRequest, request: Request) -> PredictionResponse:
    model = getattr(request.app.state, "model", None)
    scaler = getattr(request.app.state, "scaler", None)
    feature_order = getattr(request.app.state, "feature_order", FEATURE_ORDER)
    if model is None or scaler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Le modele n'est pas disponible",
        )

    try:
        features = pd.DataFrame(
            [[getattr(data, feature) for feature in feature_order]],
            columns=feature_order,
        )
        scaled_features = scaler.transform(features)
        prediction_raw = model.predict(scaled_features)[0]
        is_malignant = prediction_raw in (1, "M")

        probability = None
        if hasattr(model, "predict_proba"):
            classes = list(model.classes_)
            # Récupère l'index correspondant exactement à la classe prédite
            predicted_class_index = classes.index(prediction_raw)
            probas = model.predict_proba(scaled_features)[0]
            probability = round(float(probas[predicted_class_index]), 3)

        return PredictionResponse(
            prediction="M" if is_malignant else "B",
            label="Maligne" if is_malignant else "Bénigne",
            probability=probability,
        )
    except (ValueError, TypeError, IndexError) as error:
        logger.exception("Erreur pendant la prediction: %s", error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur pendant la prediction",
        ) from error