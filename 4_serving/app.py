import logging
import os

import joblib
import mlflow
import mlflow.sklearn
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from mlflow import MlflowClient
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TRACKING_URI = os.environ["MLFLOW_TRACKING_URI"]
MODEL_NAME   = os.environ.get("MODEL_NAME", "fraud-detector")
MODEL_ALIAS  = os.environ.get("MODEL_ALIAS", "production")

app = FastAPI(title="Fraud Detector", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

Instrumentator().instrument(app).expose(app)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")

PREDICTIONS_TOTAL = Counter(
    "fraud_predictions_total",
    "Total number of prediction requests",
)
FRAUD_DETECTED_TOTAL = Counter(
    "fraud_detected_total",
    "Total number of transactions flagged as fraud",
)
PREDICTION_AMOUNT = Histogram(
    "prediction_amount_dollars",
    "Transaction amount distribution",
    buckets=[10, 50, 100, 500, 1000, 5000, 10000],
)
PREDICTION_PROBABILITY = Histogram(
    "fraud_probability",
    "Distribution of fraud probability scores",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

_model    = None
_scaler   = None
_run_id   = None
_version  = None


@app.on_event("startup")
def load_model():
    global _model, _scaler, _run_id, _version

    mlflow.set_tracking_uri(TRACKING_URI)
    client = MlflowClient()

    log.info("Loading %s@%s from MLflow ...", MODEL_NAME, MODEL_ALIAS)
    mv = client.get_model_version_by_alias(MODEL_NAME, MODEL_ALIAS)
    _run_id  = mv.run_id
    _version = mv.version

    _model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@{MODEL_ALIAS}")
    log.info("Model loaded (run %s, version %s)", _run_id, _version)

    scaler_path = mlflow.artifacts.download_artifacts(
        run_id=_run_id, artifact_path="scaler.pkl"
    )
    _scaler = joblib.load(scaler_path)
    log.info("Scaler loaded")

    mlflow.set_experiment(f"{MODEL_NAME}-serving")


class PredictRequest(BaseModel):
    Time: float
    V1: float;  V2: float;  V3: float;  V4: float;  V5: float
    V6: float;  V7: float;  V8: float;  V9: float;  V10: float
    V11: float; V12: float; V13: float; V14: float; V15: float
    V16: float; V17: float; V18: float; V19: float; V20: float
    V21: float; V22: float; V23: float; V24: float; V25: float
    V26: float; V27: float; V28: float
    Amount: float


class PredictResponse(BaseModel):
    fraud_probability: float
    is_fraud: bool


@mlflow.trace(
    name="preprocess",
    attributes={"step": "scale_features"},
)
def preprocess(data: dict) -> np.ndarray:
    feature_names = ["Time"] + [f"V{i}" for i in range(1, 29)] + ["Amount"]
    row = np.array([[data[f] for f in feature_names]])
    row[:, [29, 0]] = _scaler.transform(row[:, [29, 0]])
    return row


@mlflow.trace(
    name="inference",
    attributes={"step": "model_predict"},
)
def inference(row: np.ndarray) -> float:
    return float(_model.predict_proba(row)[0, 1])


@app.get("/health")
def health():
    if _model is None or _scaler is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return {"status": "ok"}


@app.post("/predict", response_model=PredictResponse)
@mlflow.trace(
    name="fraud-prediction",
    attributes={
        "model_name":  MODEL_NAME,
        "model_alias": MODEL_ALIAS,
    },
)
def predict(req: PredictRequest):
    if _model is None or _scaler is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    data = req.model_dump()
    row  = preprocess(data)
    prob = inference(row)

    is_fraud = prob >= 0.5
    PREDICTIONS_TOTAL.inc()
    PREDICTION_PROBABILITY.observe(prob)
    PREDICTION_AMOUNT.observe(data["Amount"])
    if is_fraud:
        FRAUD_DETECTED_TOTAL.inc()

    mlflow.get_current_active_span().set_attributes({
        "model_version":     str(_version),
        "fraud_probability": prob,
        "is_fraud":          is_fraud,
        "amount":            data["Amount"],
    })

    return PredictResponse(fraud_probability=prob, is_fraud=is_fraud)
