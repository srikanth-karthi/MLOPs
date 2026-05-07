"""
Credit Card Fraud Detection — Training Script
Tracks params, metrics, and model artifacts via MLflow.

Usage:
    python train.py --model rf --n_estimators 200 --threshold 0.42
    python train.py --model lr --max_iter 2000
"""

import argparse
import os
import sys

import joblib
import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
from imblearn.over_sampling import SMOTE
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    classification_report,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler



def parse_args():
    parser = argparse.ArgumentParser(description="Train a fraud-detection model")

    parser.add_argument(
        "--data_path",
        type=str,
        default=os.path.join(os.path.dirname(__file__), "..", "creditcard.csv"),
        help="Path to creditcard.csv",
    )
    parser.add_argument(
        "--model",
        type=str,
        choices=["rf", "lr"],
        default="rf",
        help="Model type: rf (Random Forest) or lr (Logistic Regression)",
    )
    parser.add_argument(
        "--train_ratio",
        type=float,
        default=0.8,
        help="Fraction of data used for training (time-based split)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Classification threshold for positive class",
    )
    parser.add_argument(
        "--smote_random_state",
        type=int,
        default=42,
        help="Random state for SMOTE",
    )
    # Random Forest args
    parser.add_argument("--n_estimators", type=int, default=100)
    parser.add_argument("--max_depth", type=int, default=None)
    parser.add_argument("--rf_random_state", type=int, default=5)
    # Logistic Regression args
    parser.add_argument("--max_iter", type=int, default=1000)
    parser.add_argument("--C", type=float, default=1.0, help="Regularisation strength (LR)")

    parser.add_argument(
        "--experiment_name",
        type=str,
        default="fraud-detection",
        help="MLflow experiment name",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=os.path.join(os.path.dirname(__file__), "models"),
        help="Directory to save the serialised model",
    )
    return parser.parse_args()


# ── Data helpers ─────────────────────────────────────────────────────────────

def load_and_split(data_path: str, train_ratio: float):
    df = pd.read_csv(data_path).sort_values("Time")
    X = df.drop("Class", axis=1)
    y = df["Class"]
    split = int(len(df) * train_ratio)
    return X.iloc[:split].copy(), X.iloc[split:].copy(), y.iloc[:split], y.iloc[split:]


def scale_features(X_train: pd.DataFrame, X_test: pd.DataFrame):
    scaler = StandardScaler()
    X_train[["Amount", "Time"]] = scaler.fit_transform(X_train[["Amount", "Time"]])
    X_test[["Amount", "Time"]] = scaler.transform(X_test[["Amount", "Time"]])
    return X_train, X_test, scaler


def apply_smote(X_train, y_train, random_state: int):
    smote = SMOTE(random_state=random_state)
    return smote.fit_resample(X_train, y_train)


# ── Model builders ────────────────────────────────────────────────────────────

def build_model(args):
    if args.model == "rf":
        return RandomForestClassifier(
            n_estimators=args.n_estimators,
            max_depth=args.max_depth,
            class_weight="balanced",
            random_state=args.rf_random_state,
        )
    return LogisticRegression(
        max_iter=args.max_iter,
        C=args.C,
        class_weight="balanced",
    )


# ── Evaluation ────────────────────────────────────────────────────────────────

def evaluate(model, X_test, y_test, threshold: float) -> dict:
    y_scores = model.predict_proba(X_test)[:, 1]
    y_pred = (y_scores >= threshold).astype(int)

    precision, recall, _ = precision_recall_curve(y_test, y_scores)
    auprc = average_precision_score(y_test, y_scores)
    roc_auc = roc_auc_score(y_test, y_scores)

    cm = confusion_matrix(y_test, y_pred)
    tn, fp, fn, tp = cm.ravel()

    report = classification_report(y_test, y_pred, output_dict=True)
    fraud_f1 = report["1"]["f1-score"]
    fraud_precision = report["1"]["precision"]
    fraud_recall = report["1"]["recall"]

    print("\n── Evaluation ───────────────────────────────────")
    print(f"  AUPRC        : {auprc:.4f}")
    print(f"  ROC-AUC      : {roc_auc:.4f}")
    print(f"  Fraud F1     : {fraud_f1:.4f}")
    print(f"  Fraud Recall : {fraud_recall:.4f}")
    print(f"  Fraud Prec.  : {fraud_precision:.4f}")
    print(f"  Confusion    : TN={tn}  FP={fp}  FN={fn}  TP={tp}")
    print("─────────────────────────────────────────────────\n")

    return {
        "auprc": auprc,
        "roc_auc": roc_auc,
        "fraud_f1": fraud_f1,
        "fraud_recall": fraud_recall,
        "fraud_precision": fraud_precision,
        "true_negatives": int(tn),
        "false_positives": int(fp),
        "false_negatives": int(fn),
        "true_positives": int(tp),
    }



def main():
    args = parse_args()

    # ── MLflow setup ─────────────────────────────────────────────────────────
    mlflow.set_experiment(args.experiment_name)

    with mlflow.start_run():
        # Log all CLI params
        params = vars(args).copy()
        params.pop("data_path")   # not a hyper-parameter
        params.pop("output_dir")
        params.pop("experiment_name")
        mlflow.log_params(params)

        # ── Data ──────────────────────────────────────────────────────────────
        print(f"Loading data from {args.data_path}")
        X_train, X_test, y_train, y_test = load_and_split(args.data_path, args.train_ratio)
        mlflow.log_param("train_samples", len(X_train))
        mlflow.log_param("test_samples", len(X_test))

        X_train, X_test, scaler = scale_features(X_train, X_test)

        print("Applying SMOTE …")
        X_res, y_res = apply_smote(X_train, y_train, args.smote_random_state)

        # ── Train ─────────────────────────────────────────────────────────────
        model = build_model(args)
        print(f"Training {model.__class__.__name__} …")
        model.fit(X_res, y_res)

        # ── Evaluate ──────────────────────────────────────────────────────────
        metrics = evaluate(model, X_test, y_test, args.threshold)
        mlflow.log_metrics(metrics)

        # ── Save model ────────────────────────────────────────────────────────
        os.makedirs(args.output_dir, exist_ok=True)
        model_name = f"{args.model}_model.pkl"
        model_path = os.path.join(args.output_dir, model_name)
        scaler_path = os.path.join(args.output_dir, "scaler.pkl")

        joblib.dump(model, model_path)
        joblib.dump(scaler, scaler_path)
        print(f"Model saved to {model_path}")

        # Log artifacts and model to MLflow
        mlflow.log_artifact(model_path)
        mlflow.log_artifact(scaler_path)
        mlflow.sklearn.log_model(model, artifact_path="model")

        run_id = mlflow.active_run().info.run_id
        print(f"\nMLflow run ID : {run_id}")
        print(f"Experiment    : {args.experiment_name}")


if __name__ == "__main__":
    main()
