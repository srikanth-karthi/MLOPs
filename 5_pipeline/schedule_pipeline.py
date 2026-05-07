"""
Create (or update) a recurring KFP run for the fraud-detection pipeline.

Default: every day at 02:00 UTC.

Usage:
    # Daily schedule (default)
    python schedule_pipeline.py

    # Custom cron
    CRON="0 */6 * * *" python schedule_pipeline.py   # every 6 hours

    # Disable schedule
    python schedule_pipeline.py --disable
"""

import argparse
import datetime
import os

import kfp

KFP_ENDPOINT = os.environ.get(
    "KFP_ENDPOINT",
    "http://a9874180177eb41338621ad756a39fb9-576280531.us-east-1.elb.amazonaws.com",
)
CRON            = os.environ.get("CRON", "0 2 * * *")
JOB_NAME        = "fraud-detection-daily"
PIPELINE_NAME   = "fraud-detection-pipeline"
EXPERIMENT_NAME = "fraud-detection"

DEFAULT_ARGS = {
    "n_estimators":         100,
    "model_type":           "rf",
    "min_auprc":            0.75,
    "threshold":            0.5,
    "s3_data_key":          "data/creditcard.csv",
    "test_split_ratio":     0.2,
    "smote_random_state":   42,
    "tune_n_iter":          5,
    "n_estimators_options": "50,100,150,200",
    "max_depth_options":    "10,20,30",
}


def find_pipeline(client: kfp.Client):
    pipelines = client.list_pipelines(page_size=20).pipelines or []
    for p in pipelines:
        if p.display_name == PIPELINE_NAME:
            return p
    raise RuntimeError(f"Pipeline '{PIPELINE_NAME}' not found — run upload_pipeline.py first")


def find_latest_version(client: kfp.Client, pipeline_id: str) -> str:
    versions = client.list_pipeline_versions(pipeline_id=pipeline_id, page_size=10)
    latest = sorted(versions.pipeline_versions or [], key=lambda v: v.created_at, reverse=True)
    if not latest:
        raise RuntimeError("No pipeline versions found")
    return latest[0].pipeline_version_id


def find_existing_job(client: kfp.Client, experiment_id: str):
    jobs = client.list_recurring_runs(experiment_id=experiment_id, page_size=50)
    for job in jobs.recurring_runs or []:
        if job.display_name == JOB_NAME:
            return job
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--disable", action="store_true", help="Disable the recurring schedule")
    args = parser.parse_args()

    client     = kfp.Client(host=KFP_ENDPOINT)
    pipeline   = find_pipeline(client)
    version_id = find_latest_version(client, pipeline.pipeline_id)
    experiment = client.create_experiment(name=EXPERIMENT_NAME)
    existing   = find_existing_job(client, experiment.experiment_id)

    if args.disable:
        if existing:
            client.disable_recurring_run(existing.recurring_run_id)
            print(f"Disabled recurring job '{JOB_NAME}' ({existing.recurring_run_id})")
        else:
            print("No existing schedule found — nothing to disable")
        return

    if existing:
        client.delete_recurring_run(existing.recurring_run_id)
        print(f"Replaced existing schedule '{JOB_NAME}'")

    job = client.create_recurring_run(
        experiment_id=experiment.experiment_id,
        job_name=JOB_NAME,
        pipeline_id=pipeline.pipeline_id,
        version_id=version_id,
        cron_expression=CRON,
        arguments=DEFAULT_ARGS,
        enabled=True,
        no_catchup=True,
        max_concurrency=1,
    )

    print(f"Scheduled '{JOB_NAME}'")
    print(f"  Cron:     {CRON}")
    print(f"  Pipeline: {PIPELINE_NAME} (version {version_id[:8]}...)")
    print(f"  Job ID:   {job.recurring_run_id}")
    print(f"  Track at: {KFP_ENDPOINT}/#/recurringruns/details/{job.recurring_run_id}")


if __name__ == "__main__":
    main()
