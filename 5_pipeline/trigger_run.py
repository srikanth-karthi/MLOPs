"""
Trigger a new run from the already-uploaded fraud-detection-pipeline.

Usage:
    KFP_ENDPOINT=http://<kfp-elb> python trigger_run.py
"""

import os
import kfp

KFP_ENDPOINT = os.environ.get(
    "KFP_ENDPOINT",
    "http://a9874180177eb41338621ad756a39fb9-576280531.us-east-1.elb.amazonaws.com",
)

client = kfp.Client(host=KFP_ENDPOINT)

# Find the existing uploaded pipeline by name
pipelines = client.list_pipelines(page_size=10)
pipeline_id = None
for p in pipelines.pipelines or []:
    if p.display_name == "fraud-detection-pipeline":
        pipeline_id = p.pipeline_id
        break

if not pipeline_id:
    raise RuntimeError("Pipeline 'fraud-detection-pipeline' not found — run upload_pipeline.py first")

print(f"Found pipeline: {pipeline_id}")

experiment = client.create_experiment(name="fraud-detection")

arguments = {
    # ── Model ──────────────────────────────────────────────────────────────
    "n_estimators":        100,
    "model_type":          "rf",        # rf | xgb | lgbm
    "min_auprc":           0.75,
    "threshold":           0.5,
    # ── Data ───────────────────────────────────────────────────────────────
    "s3_data_key":         "data/creditcard.csv",
    "test_split_ratio":    0.2,
    "smote_random_state":  42,
    # ── Drift ──────────────────────────────────────────────────────────────
    "drift_threshold":     0.05,        # lower = more sensitive
    # ── Tuning ─────────────────────────────────────────────────────────────
    "tune_n_iter":         5,
    "n_estimators_options": "50,100,150,200",
    "max_depth_options":   "10,20,30",
}

run = client.create_run_from_pipeline_package(
    pipeline_file="compiled/pipeline.yaml",
    arguments=arguments,
    run_name=f"fraud-detection-run-{__import__('datetime').datetime.now().strftime('%Y%m%d-%H%M%S')}",
    experiment_id=experiment.experiment_id,
    enable_caching=False,
)
print(f"Run started: {run.run_id}")
print(f"Track at: {KFP_ENDPOINT}/#/runs/details/{run.run_id}")
