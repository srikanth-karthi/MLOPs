# Credit Card Fraud Detection — End-to-End MLOps on AWS EKS

A production-grade MLOps platform for real-time credit card fraud detection. Covers the full lifecycle: exploratory analysis → automated training pipeline → model registry → live serving with canary deployments → observability.

---

## Architecture

### AWS EKS Cluster

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS EKS Cluster                                │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                     Kubeflow Pipelines                               │   │
│  │                                                                      │   │
│  │   ┌────────┐   ┌────────┐   ┌──────────┐   ┌─────────────────────┐   │   │
│  │   │  Tune  │──▶│ Train  │──▶│ Evaluate │──▶│  MLflow Evaluate    │   │   │
│  │   │  (CV)  │   │RF/XGB/ │   │AUPRC gate│   │ (batch metrics log) │   │   │
│  │   └────────┘   │  LGBM  │   └──────────┘   └──────────┬──────────┘   │   │
│  │                └────────┘        │ fail            pass │            │   │
│  │                                  ▼                      ▼            │   │
│  │                             [pipeline           ┌──────────────┐     │   │
│  │                              stops]             │   Register   │     │   │
│  │                                                 │ @production  │     │   │
│  │                                                 └──────┬───────┘     │   │
│  │                                                        │             │   │
│  │                                              ┌─────────▼────────     │   │
│  │                                              │  Canary Deploy   │    │   │
│  │                                              │ 1 replica health │    │   │
│  │                                              │ check → promote  │    │   │
│  │                                              └─────────┬────────┘    │   │
│  └────────────────────────────────────────────────────────│─────────────┘   │
│                                                           │                 │
│  ┌────────────────────────────────────────────────────────▼─────────────┐   │
│  │                    Serving  (namespace: mlflow)                      │   │
│  │                                                                      │   │
│  │   Internet ──▶ LoadBalancer ──▶ fraud-detector pods (FastAPI)        │   │
│  │                                        │                             │   │
│  │                              ┌─────────┴──────────┐                  │   │
│  │                              │   MLflow Tracing   │                  │   │
│  │                              │  /predict traced   │                  │   │
│  │                              │  per-span metrics  │                  │   │
│  │                              └─────────┬──────────┘                  │   │
│  │                                        │ /metrics                    │   │
│  │                                        ▼                             │   │
│  │                              Prometheus scrape (15s)                 │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌───────────────────┐   ┌──────────────────┐   ┌────────────────────────┐  │
│  │      MLflow       │   │      MinIO       │   │  Prometheus + Grafana  │  │
│  │  Tracking Server  │   │  Artifact Store  │   │   (kube-prometheus-    │  │
│  │  Model Registry   │   │  Pipeline Logs   │   │        stack)          │  │
│  └───────────────────┘   └──────────────────┘   └────────────────────────┘  │
│                                                                             │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    AWS Services                                        │ │
│  │   S3 (training data + MLflow artifacts)   EBS CSI (persistent volumes) │ │
│  │   IAM (IRSA — pod-level S3 access)        EKS Managed Node Groups      │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Pipeline Flow

```
Scheduled daily @ 02:00 UTC
OR manual: MODEL_TYPE=xgb python trigger_run.py

    Tune --> Train --> Evaluate
    (rf/xgb/           |       \
     lgbm/lr)        fail      pass
                       |         \
                    (stop)    MLflow Evaluate
                                   |
                               Register (@staging)
                                   |
                           Canary Deploy (1 replica)
                            /                \
                      ready                timeout
                        |                     |
               set @production         revert @production
               archive old version     to previous version
               rolling restart         delete canary
```

---

## Repository Structure

```
Mlops/
├── 1_notebook/                        # Exploratory analysis
│   └── train_model.ipynb              # EDA, feature analysis, baseline model
│
├── 2_training/                        # Standalone training job (pre-pipeline)
│   ├── train.py                       # Training script run as a K8s Job
│   ├── Dockerfile                     # Training container image
│   └── requirements.txt
│
├── 3_registry/
│   └── register_model.py              # One-off script to register a model in MLflow
│
├── 4_serving/                         # Real-time inference service
│   ├── app.py                         # FastAPI app — /predict, /health, /metrics
│   ├── Dockerfile
│   ├── requirements.txt
│   └── static/
│       └── index.html                 # Browser UI with 6 fraud/legit scenarios
│
├── 5_pipeline/                        # Kubeflow Pipelines
│   ├── pipeline.py                    # Pipeline definition (compile → pipeline.yaml)
│   ├── upload_pipeline.py             # Upload compiled YAML to KFP server
│   ├── trigger_run.py                 # Manually trigger a one-off run with custom params
│   ├── schedule_pipeline.py           # Register/update daily recurring KFP run
│   ├── Dockerfile                     # Base image for all pipeline components
│   └── components/
│       ├── tune_component.py          # RandomizedSearchCV — model-specific search spaces (rf/xgb/lgbm/lr)
│       ├── train_component.py         # Train RF / XGBoost / LightGBM / LR, log to MLflow
│       ├── evaluate_component.py      # Quality gate — blocks promotion if AUPRC < threshold
│       ├── mlflow_evaluate_component.py  # mlflow.evaluate() — logs full classifier metrics
│       ├── register_component.py      # Register model in MLflow Registry, set @staging alias
│       └── deploy_component.py        # Canary deploy — promotes to @production or reverts on failure
│
├── 6_k8s/                             # Raw Kubernetes manifests (reference / manual apply)
│   ├── deployment.yaml                # fraud-detector Deployment
│   ├── service.yaml                   # LoadBalancer Service
│   ├── hpa.yaml                       # HorizontalPodAutoscaler (1–10 replicas, 60% CPU)
│   └── training-job.yaml              # One-off K8s Job for training
│
├── 7_infra/                           # Reserved for additional infra scripts
│
└── terraform/
    └── eks/
        ├── versions.tf                # Provider versions and backend config
        ├── variables.tf               # Input variables (region, cluster name, sizes)
        ├── terraform.tfvars           # Actual variable values (gitignored)
        ├── vpc.tf                     # VPC, subnets, NAT gateway
        ├── eks.tf                     # EKS cluster, node groups, IMDS hop limit fix
        ├── ebs-csi.tf                 # EBS CSI driver add-on for persistent volumes
        ├── s3.tf                      # S3 bucket for MLflow artifacts and training data
        ├── mlflow.tf                  # MLflow Helm chart (MySQL backend + MinIO)
        ├── kubeflow.tf                # Kubeflow Pipelines Helm chart + pipeline compile trigger
        ├── serving.tf                 # fraud-detector Deployment, Service, HPA, RBAC
        ├── monitoring.tf              # kube-prometheus-stack + Grafana + ServiceMonitor
        ├── training-job.tf            # K8s Job resource for standalone training
        ├── outputs.tf                 # Cluster endpoint, load balancer URLs
        └── grafana-dashboard.json     # Pre-built Grafana dashboard for fraud detector
```

---

## Key Design Decisions

### Infrastructure
- **EKS managed node groups** — two groups: `general` (Kubeflow + MLflow) and `serving` (fraud-detector). Separated so pipeline jobs don't compete with live traffic.
- **IMDS hop limit = 2** — allows containers inside pods to reach the EC2 instance metadata service for IAM credentials. Default of 1 blocks containers.
- **EBS CSI driver** — required for MLflow's MySQL and MinIO persistent volumes. Volumes are AZ-pinned, so nodes must be in the same AZ.
- **S3 for artifacts** — MLflow stores model artifacts in S3 rather than on-disk, so any pod can download them at serving time.

### Pipeline
- **Pre-built base image** — all pipeline components use `srikanthkarthi/mlops-pipeline-base:latest` instead of installing packages at runtime. Eliminates the 3–5 minute pip install overhead per step.
- **Four model types** — RF, XGBoost, LightGBM, and Logistic Regression are all supported. Each has its own hyperparameter search space in the tune step. Switch via `MODEL_TYPE=xgb python trigger_run.py`.
- **Hyperparameter tuning first** — `RandomizedSearchCV` with `StratifiedKFold` runs before training so the train step always uses the best found parameters.
- **Quality gate** — the `evaluate` step compares AUPRC against a configurable threshold. If the model doesn't clear the bar, the pipeline stops — nothing gets registered or deployed.
- **Scheduled, not drift-gated** — the pipeline runs on a daily cron via KFP recurring runs. Drift detection was removed because a fixed schedule is simpler and predictable for a POC.
- **MLMD artifact association** — runs are triggered via `client.run_pipeline()` (not `create_run_from_pipeline_package()`), which links runs to the registered pipeline in KFP's ML Metadata store. Artifacts show the correct pipeline name instead of `[unknown]`.

### Serving
- **Model loaded at startup** — the FastAPI app pulls `fraud-detector@production` from MLflow Registry when the pod starts. No model files baked into the image.
- **Canary deployment with rollback** — the `deploy` step separates promotion into two phases. First, `register` sets the new model to `@staging` only. Then `deploy` creates a 1-replica canary pod. If it becomes Ready within 120s, `@production` is promoted to the new version, the old version is archived, and stable is rolling-restarted. If the canary times out, `@production` is explicitly reverted to the previous version in MLflow Registry, the canary is deleted, and stable keeps serving the old model unchanged.
- **MLflow Tracing** — every `/predict` call is wrapped with `@mlflow.trace`, recording preprocessing and inference as child spans. Traces are visible in the MLflow UI.
- **Prometheus metrics** — `/metrics` endpoint exposes prediction count, fraud detection count, probability distribution, and transaction amount distribution. Scraped every 15s by Prometheus via a `ServiceMonitor`.

### Observability
- **Grafana dashboard** — pre-provisioned at deploy time via a ConfigMap. Shows prediction rate, fraud detection rate, latency percentiles (p50/p95/p99), probability score distribution, transaction amount distribution, error rate, and service health.
- **MLflow Experiment tracking** — every training run logs hyperparameters, AUPRC, F1, ROC-AUC, confusion matrix, and the scaler artifact alongside the model.

---

## Triggering Runs

**Upload a new pipeline version and start a run:**
```bash
python 5_pipeline/pipeline.py            # compile
python 5_pipeline/upload_pipeline.py     # upload + run (uses rf defaults)
```

**Trigger a one-off run against the already-uploaded pipeline:**
```bash
# default (Random Forest)
python 5_pipeline/trigger_run.py

# try a different model type
MODEL_TYPE=xgb  python 5_pipeline/trigger_run.py
MODEL_TYPE=lgbm python 5_pipeline/trigger_run.py
MODEL_TYPE=lr   python 5_pipeline/trigger_run.py

# override other params
MODEL_TYPE=xgb MIN_AUPRC=0.80 TUNE_N_ITER=10 python 5_pipeline/trigger_run.py
```

**Schedule daily recurring runs (02:00 UTC):**
```bash
python 5_pipeline/schedule_pipeline.py
```

**Compare model types** — go to KFP UI → Experiments → fraud-detection → select runs → Compare runs.

---

## Dataset

[Kaggle — Credit Card Fraud Detection](https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud)

284,807 transactions, 492 fraud cases (0.17%). Features V1–V28 are PCA-transformed for anonymisation. `Time` and `Amount` are raw and scaled at training time; the fitted scaler is saved as an MLflow artifact so serving applies identical transformations.

---

## Grafana Dashboard Panels

| Panel | What it shows |
|---|---|
| Prediction Rate | Requests per second to `/predict` |
| Fraud Detection Rate | % of transactions flagged as fraud |
| Prediction Latency | p50 / p95 / p99 end-to-end latency |
| Fraud Probability Distribution | Histogram of model confidence scores |
| Transaction Amount Distribution | p50 / p95 of dollar amounts seen |
| Error Rate | 4xx and 5xx per second |
| Service Health | Up/down status of fraud-detector pod |
