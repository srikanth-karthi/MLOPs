from kfp import dsl


@dsl.component(base_image="srikanthkarthi/mlops-pipeline-base:latest")
def check_drift(
    mlflow_tracking_uri: str,
    s3_bucket: str,
    aws_region: str,
    s3_data_key: str = "data/creditcard.csv",
    model_name: str = "fraud-detector",
    drift_threshold: float = 0.05,
) -> bool:
    """
    KS-test each feature in new data against the training distribution saved
    in the production model's MLflow run. Returns True if drift is detected
    (or if no production model exists yet — triggers initial training).
    """
    import json
    import os

    import boto3
    import numpy as np
    import pandas as pd
    from scipy import stats
    import mlflow
    from mlflow import MlflowClient
    from mlflow.exceptions import MlflowException

    os.environ["AWS_DEFAULT_REGION"] = aws_region
    mlflow.set_tracking_uri(mlflow_tracking_uri)
    client = MlflowClient()

    # ── Load reference statistics from last production run ────────────────────
    try:
        mv = client.get_model_version_by_alias(model_name, "production")
        run_id = mv.run_id
        stats_path = mlflow.artifacts.download_artifacts(
            run_id=run_id, artifact_path="reference_stats.json"
        )
        with open(stats_path) as f:
            ref_stats = json.load(f)
        print(f"Loaded reference stats from run {run_id}")
    except MlflowException:
        print("No production model found — triggering initial training.")
        return True

    # ── Load new data from S3 (last 20% of CSV = held-out window) ────────────
    s3 = boto3.client("s3", region_name=aws_region)
    print(f"Downloading s3://{s3_bucket}/{s3_data_key}")
    s3.download_file(s3_bucket, s3_data_key, "/tmp/data.csv")
    df = pd.read_csv("/tmp/data.csv")
    new_data = df.iloc[int(len(df) * 0.8):]

    # ── KS-test each feature ──────────────────────────────────────────────────
    feature_cols = ["Time"] + [f"V{i}" for i in range(1, 29)] + ["Amount"]
    drifted = []

    for col in feature_cols:
        ref_mean = ref_stats[col]["mean"]
        ref_std  = max(ref_stats[col]["std"], 1e-9)
        ref_samples = np.random.normal(ref_mean, ref_std, 1000)
        _, p_value = stats.ks_2samp(ref_samples, new_data[col].dropna().values)
        if p_value < drift_threshold:
            drifted.append(f"{col}(p={p_value:.4f})")

    if drifted:
        print(f"Drift detected in {len(drifted)} features: {', '.join(drifted)}")
        return True

    print(f"No significant drift detected across {len(feature_cols)} features.")
    return False
