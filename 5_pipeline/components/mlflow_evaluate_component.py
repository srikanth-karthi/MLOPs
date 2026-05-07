from kfp import dsl


@dsl.component(base_image="srikanthkarthi/mlops-pipeline-base:latest")
def mlflow_evaluate(
    mlflow_tracking_uri: str,
    run_id: str,
    s3_bucket: str,
    aws_region: str,
    s3_data_key: str = "data/creditcard.csv",
    test_split_ratio: float = 0.2,
    model_name: str = "fraud-detector",
) -> str:
    import os
    import boto3
    import mlflow
    import numpy as np
    import pandas as pd
    from mlflow import MlflowClient

    os.environ["AWS_DEFAULT_REGION"]   = aws_region
    os.environ["GIT_PYTHON_REFRESH"]   = "quiet"
    os.environ["MLFLOW_LOGGING_LEVEL"] = "WARNING"

    mlflow.set_tracking_uri(mlflow_tracking_uri)

    s3 = boto3.client("s3", region_name=aws_region)
    print(f"Downloading s3://{s3_bucket}/{s3_data_key}")
    s3.download_file(s3_bucket, s3_data_key, "/tmp/data.csv")

    df    = pd.read_csv("/tmp/data.csv").sort_values("Time")
    split = int(len(df) * (1 - test_split_ratio))
    test  = df.iloc[split:].copy()

    import joblib
    scaler_path = mlflow.artifacts.download_artifacts(
        run_id=run_id, artifact_path="scaler.pkl"
    )
    scaler = joblib.load(scaler_path)
    test[["Amount", "Time"]] = scaler.transform(test[["Amount", "Time"]])

    X_test = test.drop("Class", axis=1)
    y_test = test["Class"].astype(int)

    model_uri = f"runs:/{run_id}/model"

    eval_data = X_test.copy()
    eval_data["label"] = y_test.values

    with mlflow.start_run(run_name=f"eval-{run_id[:8]}") as eval_run:
        results = mlflow.evaluate(
            model=model_uri,
            data=eval_data,
            targets="label",
            model_type="classifier",
            evaluators="default",
        )

        print(f"Accuracy:  {results.metrics.get('accuracy_score', 'N/A'):.4f}")
        print(f"F1:        {results.metrics.get('f1_score', 'N/A'):.4f}")
        print(f"Precision: {results.metrics.get('precision_score', 'N/A'):.4f}")
        print(f"Recall:    {results.metrics.get('recall_score', 'N/A'):.4f}")
        print(f"ROC-AUC:   {results.metrics.get('roc_auc', 'N/A'):.4f}")

        eval_run_id = eval_run.info.run_id

    print(f"Evaluation run ID: {eval_run_id}")
    return eval_run_id
