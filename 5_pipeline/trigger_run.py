"""
Trigger a new run from the already-uploaded fraud-detection-pipeline.

Usage:
    KFP_ENDPOINT=http://<kfp-elb> python trigger_run.py
"""

import os
from datetime import datetime

import kfp

KFP_ENDPOINT = os.environ.get(
    "KFP_ENDPOINT",
    "http://a9874180177eb41338621ad756a39fb9-576280531.us-east-1.elb.amazonaws.com",
)

PIPELINE_NAME = "fraud-detection-pipeline"

client = kfp.Client(host=KFP_ENDPOINT)

pipelines = client.list_pipelines(page_size=100)
pipeline_id = None
for p in (pipelines.pipelines or []):
    if p.display_name == PIPELINE_NAME:
        pipeline_id = p.pipeline_id
        break

if not pipeline_id:
    raise RuntimeError(f"Pipeline '{PIPELINE_NAME}' not found — run upload_pipeline.py first")

print(f"Found pipeline: {pipeline_id}")

versions = client.list_pipeline_versions(pipeline_id=pipeline_id, page_size=1)
version_id = versions.pipeline_versions[0].pipeline_version_id if versions.pipeline_versions else None

experiment = client.create_experiment(name="fraud-detection")

params = {
    "n_estimators":         100,
    "model_type":           "rf",   # rf | xgb | lgbm | lr
    "min_auprc":            0.75,
    "threshold":            0.5,
    "s3_data_key":          "data/creditcard.csv",
    "test_split_ratio":     0.2,
    "smote_random_state":   42,
    "tune_n_iter":          5,
    "n_estimators_options": "50,100,150,200",
    "max_depth_options":    "10,20,30",
}

run = client.run_pipeline(
    experiment_id=experiment.experiment_id,
    job_name=f"fraud-detection-run-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
    pipeline_id=pipeline_id,
    version_id=version_id,
    params=params,
    enable_caching=False,
)
print(f"Run started: {run.run_id}")
print(f"Track at: {KFP_ENDPOINT}/#/runs/details/{run.run_id}")
