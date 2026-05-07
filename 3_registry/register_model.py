"""
Promote the best fraud-detection run to the MLflow Model Registry.

Finds the run with the highest AUPRC in the 'fraud-detection' experiment,
validates against a minimum threshold, registers as 'fraud-detector', and
sets the 'production' alias (replacing the previous holder).

Usage:
    MLFLOW_TRACKING_URI=http://<host>:5000 python register_model.py
"""

import os
import sys

import mlflow
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

EXPERIMENT_NAME = "fraud-detection"
MODEL_NAME      = "fraud-detector"
MIN_AUPRC       = 0.75


def get_best_run(client: MlflowClient, experiment_id: str):
    runs = client.search_runs(
        experiment_ids=[experiment_id],
        filter_string="status = 'FINISHED'",
        order_by=["metrics.auprc DESC"],
        max_results=1,
    )
    if not runs:
        sys.exit("No finished runs found in experiment")
    return runs[0]


def main():
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        sys.exit("MLFLOW_TRACKING_URI is not set")

    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient()

    # ── Find experiment ───────────────────────────────────────────────────────
    experiment = client.get_experiment_by_name(EXPERIMENT_NAME)
    if experiment is None:
        sys.exit(f"Experiment '{EXPERIMENT_NAME}' not found")

    # ── Best run by AUPRC ─────────────────────────────────────────────────────
    best = get_best_run(client, experiment.experiment_id)
    run_id   = best.info.run_id
    auprc    = best.data.metrics.get("auprc", 0.0)
    roc_auc  = best.data.metrics.get("roc_auc", 0.0)
    fraud_f1 = best.data.metrics.get("fraud_f1", 0.0)

    print(f"Best run  : {run_id}")
    print(f"AUPRC     : {auprc:.4f}")
    print(f"ROC-AUC   : {roc_auc:.4f}")
    print(f"Fraud F1  : {fraud_f1:.4f}")

    # ── Quality gate ──────────────────────────────────────────────────────────
    if auprc < MIN_AUPRC:
        sys.exit(f"AUPRC {auprc:.4f} < threshold {MIN_AUPRC} — not registering")

    # ── Track current production version so we can archive it ─────────────────
    prev_prod_version = None
    try:
        prev = client.get_model_version_by_alias(MODEL_NAME, "production")
        prev_prod_version = prev.version
    except MlflowException:
        pass  # no production version yet — first registration

    # ── Register new version ──────────────────────────────────────────────────
    mv = mlflow.register_model(f"runs:/{run_id}/model", MODEL_NAME)
    version = mv.version
    print(f"\nRegistered '{MODEL_NAME}' v{version}")

    # ── Staging → Production ──────────────────────────────────────────────────
    client.set_registered_model_alias(MODEL_NAME, "staging", version)
    print(f"Alias 'staging'    → v{version}")

    client.set_registered_model_alias(MODEL_NAME, "production", version)
    print(f"Alias 'production' → v{version}")

    # Tag the old production version as archived
    if prev_prod_version and prev_prod_version != version:
        client.set_model_version_tag(MODEL_NAME, prev_prod_version, "archived", "true")
        print(f"Archived previous production v{prev_prod_version}")

    print(f"\nLoad with: mlflow.pyfunc.load_model('models:/{MODEL_NAME}@production')")


if __name__ == "__main__":
    main()
