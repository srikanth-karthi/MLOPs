"""
Upload compiled pipeline.yaml to Kubeflow Pipelines and trigger a run.
Creates the pipeline on first run; uploads a new version on subsequent runs.

Usage:
    KFP_ENDPOINT=http://<kfp-elb> python upload_pipeline.py
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

# Find existing pipeline by name
pipelines = client.list_pipelines(page_size=100)
pipeline_id = None
for p in (pipelines.pipelines or []):
    if p.display_name == PIPELINE_NAME:
        pipeline_id = p.pipeline_id
        break

if pipeline_id is None:
    pipeline = client.upload_pipeline(
        pipeline_package_path="compiled/pipeline.yaml",
        pipeline_name=PIPELINE_NAME,
    )
    pipeline_id = pipeline.pipeline_id
    print(f"Pipeline created: {pipeline_id}")
else:
    version = client.upload_pipeline_version(
        pipeline_package_path="compiled/pipeline.yaml",
        pipeline_version_name=f"v-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        pipeline_id=pipeline_id,
    )
    print(f"Pipeline version uploaded: {version.pipeline_version_id}")

# Trigger a run under the fraud-detection experiment
experiment = client.create_experiment(name="fraud-detection")

run = client.create_run_from_pipeline_package(
    pipeline_file="compiled/pipeline.yaml",
    arguments={"n_estimators": 100, "min_auprc": 0.75},
    run_name=f"fraud-detection-run-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
    experiment_id=experiment.experiment_id,
    enable_caching=False,
)
print(f"Run started: {run.run_id}")
print(f"Track at: {KFP_ENDPOINT}/#/runs/details/{run.run_id}")
