"""
Fraud detection Kubeflow Pipeline.

Steps:
    check_drift  → detect data drift vs production model baseline
    tune         → RandomizedSearchCV for best RF hyperparameters
    train        → train RandomForest with best params, log to MLflow
    evaluate     → quality gate: AUPRC >= min_auprc
    register     → promote model to @production
    deploy       → rolling restart of serving Deployment

Compile:
    python pipeline.py

Upload and run:
    python upload_pipeline.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from kfp import compiler, dsl

from components.deploy_component import deploy
from components.drift_component import check_drift
from components.evaluate_component import evaluate
from components.mlflow_evaluate_component import mlflow_evaluate
from components.register_component import register
from components.train_component import train
from components.tune_component import tune

MLFLOW_INTERNAL = "http://mlflow.mlflow.svc.cluster.local:5000"
S3_BUCKET       = "mlops-cluster-mlflow-853973692277"
AWS_REGION      = "us-east-1"


@dsl.pipeline(
    name="fraud-detection-pipeline",
    description="Drift check → Tune → Train → Evaluate → Register → Deploy",
)
def fraud_detection_pipeline(
    mlflow_tracking_uri: str      = MLFLOW_INTERNAL,
    s3_bucket: str                = S3_BUCKET,
    s3_data_key: str              = "data/creditcard.csv",
    aws_region: str               = AWS_REGION,
    experiment_name: str          = "fraud-detection",
    model_name: str               = "fraud-detector",
    n_estimators: int             = 100,
    threshold: float              = 0.5,
    test_split_ratio: float       = 0.2,
    smote_random_state: int       = 42,
    min_auprc: float              = 0.75,
    model_type: str               = "rf",
    drift_threshold: float        = 0.05,
    tune_n_iter: int              = 5,
    n_estimators_options: str     = "50,100,150,200",
    max_depth_options: str        = "10,20,30",
):
    drift_task = check_drift(
        mlflow_tracking_uri=mlflow_tracking_uri,
        s3_bucket=s3_bucket,
        aws_region=aws_region,
        s3_data_key=s3_data_key,
        model_name=model_name,
        drift_threshold=drift_threshold,
    )

    with dsl.If(drift_task.output == True, name="retrain-on-drift"):
        tune_task = tune(
            s3_bucket=s3_bucket,
            aws_region=aws_region,
            s3_data_key=s3_data_key,
            n_iter=tune_n_iter,
            n_estimators_options=n_estimators_options,
            max_depth_options=max_depth_options,
        )
        tune_task.set_cpu_request("1500m").set_memory_request("5Gi")

        train_task = train(
            mlflow_tracking_uri=mlflow_tracking_uri,
            s3_bucket=s3_bucket,
            aws_region=aws_region,
            s3_data_key=s3_data_key,
            experiment_name=experiment_name,
            n_estimators=n_estimators,
            threshold=threshold,
            test_split_ratio=test_split_ratio,
            smote_random_state=smote_random_state,
            model_type=model_type,
            best_params=tune_task.output,
        )
        train_task.set_cpu_request("1500m").set_memory_request("5Gi")

        evaluate_task = evaluate(
            mlflow_tracking_uri=mlflow_tracking_uri,
            run_id=train_task.output,
            min_auprc=min_auprc,
        )

        with dsl.If(evaluate_task.output == True, name="quality-gate"):
            mlflow_evaluate(
                mlflow_tracking_uri=mlflow_tracking_uri,
                run_id=train_task.output,
                s3_bucket=s3_bucket,
                aws_region=aws_region,
                s3_data_key=s3_data_key,
                test_split_ratio=test_split_ratio,
                model_name=model_name,
            )

            register_task = register(
                mlflow_tracking_uri=mlflow_tracking_uri,
                run_id=train_task.output,
                model_name=model_name,
            )
            deploy(
                model_name=model_name,
                model_version=register_task.output,
            )


if __name__ == "__main__":
    os.makedirs("compiled", exist_ok=True)
    compiler.Compiler().compile(
        pipeline_func=fraud_detection_pipeline,
        package_path="compiled/pipeline.yaml",
    )
    print("Compiled → compiled/pipeline.yaml")
