from kfp import dsl


@dsl.component(base_image="srikanthkarthi/mlops-training:latest")
def train(
    mlflow_tracking_uri: str,
    s3_bucket: str,
    aws_region: str,
    experiment_name: str,
    n_estimators: int,
    threshold: float,
    s3_data_key: str = "data/creditcard.csv",
    test_split_ratio: float = 0.2,
    smote_random_state: int = 42,
    model_type: str = "rf",
    best_params: str = "{}",
) -> str:
    import json
    import os
    import joblib
    import mlflow
    import mlflow.sklearn
    import numpy as np
    import pandas as pd
    import boto3
    from imblearn.over_sampling import SMOTE
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import (
        average_precision_score,
        classification_report,
        roc_auc_score,
    )
    from sklearn.preprocessing import StandardScaler

    os.environ["AWS_DEFAULT_REGION"]   = aws_region
    os.environ["GIT_PYTHON_REFRESH"]   = "quiet"
    os.environ["MLFLOW_LOGGING_LEVEL"] = "WARNING"

    params = json.loads(best_params) if best_params and best_params != "{}" else {}
    n_estimators = params.get("n_estimators", n_estimators)
    print(f"Training with params: {params if params else 'defaults'}")

    s3 = boto3.client("s3", region_name=aws_region)
    print(f"Downloading s3://{s3_bucket}/{s3_data_key}")
    s3.download_file(s3_bucket, s3_data_key, "/tmp/data.csv")
    print("Data downloaded from S3")

    mlflow.set_tracking_uri(mlflow_tracking_uri)
    mlflow.set_experiment(experiment_name)

    # time-based split to prevent leakage
    df = pd.read_csv("/tmp/data.csv").sort_values("Time")
    X = df.drop("Class", axis=1)
    y = df["Class"]
    split = int(len(df) * (1 - test_split_ratio))
    X_train, X_test = X.iloc[:split].copy(), X.iloc[split:].copy()
    y_train, y_test = y.iloc[:split], y.iloc[split:]

    scaler = StandardScaler()
    X_train[["Amount", "Time"]] = scaler.fit_transform(X_train[["Amount", "Time"]])
    X_test[["Amount", "Time"]] = scaler.transform(X_test[["Amount", "Time"]])

    X_res, y_res = SMOTE(random_state=smote_random_state).fit_resample(X_train, y_train)

    base_params = {"n_estimators": n_estimators, "class_weight": "balanced", "random_state": 5}
    base_params.update({k: v for k, v in params.items() if k != "n_estimators"})

    if model_type == "rf":
        from sklearn.ensemble import RandomForestClassifier
        model = RandomForestClassifier(**base_params)
    elif model_type == "xgb":
        from xgboost import XGBClassifier
        base_params.pop("class_weight", None)
        base_params["scale_pos_weight"] = int((y_res == 0).sum() / (y_res == 1).sum())
        model = XGBClassifier(**base_params, eval_metric="aucpr", use_label_encoder=False)
    elif model_type == "lgbm":
        from lightgbm import LGBMClassifier
        model = LGBMClassifier(**base_params)
    elif model_type == "lr":
        from sklearn.linear_model import LogisticRegression
        lr_params = {k: v for k, v in base_params.items() if k in ("class_weight", "random_state")}
        model = LogisticRegression(max_iter=1000, solver="lbfgs", **lr_params)
    else:
        raise ValueError(f"Unknown model_type '{model_type}'. Choose: rf, xgb, lgbm, lr")

    model.fit(X_res, y_res)

    with mlflow.start_run() as run:
        mlflow.log_params({
            "model": model_type,
            "n_estimators": n_estimators,
            "threshold": threshold,
            "test_split_ratio": test_split_ratio,
            "smote_random_state": smote_random_state,
            "s3_data_key": s3_data_key,
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            **{f"tuned_{k}": v for k, v in params.items()},
        })

        y_scores = model.predict_proba(X_test)[:, 1]
        y_pred = (y_scores >= threshold).astype(int)
        report = classification_report(y_test, y_pred, output_dict=True)

        metrics = {
            "auprc": average_precision_score(y_test, y_scores),
            "roc_auc": roc_auc_score(y_test, y_scores),
            "fraud_f1": report["1"]["f1-score"],
            "fraud_recall": report["1"]["recall"],
            "fraud_precision": report["1"]["precision"],
        }
        mlflow.log_metrics(metrics)
        print(f"AUPRC={metrics['auprc']:.4f}  ROC-AUC={metrics['roc_auc']:.4f}")

        joblib.dump(scaler, "/tmp/scaler.pkl")
        mlflow.log_artifact("/tmp/scaler.pkl")
        mlflow.sklearn.log_model(model, artifact_path="model")

        # Save training distribution stats for drift detection
        ref_stats = {
            col: {"mean": float(X_train[col].mean()), "std": float(X_train[col].std())}
            for col in X_train.columns
        }
        with open("/tmp/reference_stats.json", "w") as f:
            import json as _json
            _json.dump(ref_stats, f)
        mlflow.log_artifact("/tmp/reference_stats.json")

        run_id = run.info.run_id

    print(f"Run ID: {run_id}")
    return run_id
