# Credit Card Fraud Detection — End-to-End MLOps on AWS EKS

A production-grade MLOps platform for real-time credit card fraud detection. Covers the full lifecycle: exploratory analysis → automated training pipeline → model registry → live serving with canary deployments → observability.

---

## Architecture

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
[Scheduled daily @ 02:00 UTC]
        │
        ▼
   Tune ──▶ Train ──▶ Quality Gate (AUPRC ≥ 0.75)
                              │
                    ┌─────────┴──────────┐
                  fail                 pass
                    │                   │
               [stop]            MLflow Evaluate
                                        │
                                    Register
                                  @production
                                        │
                                 Canary Deploy
                              (health check → promote)
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
│       ├── tune_component.py          # RandomizedSearchCV hyperparameter search
│       ├── train_component.py         # Train RF / XGBoost / LightGBM, log to MLflow
│       ├── evaluate_component.py      # Quality gate — blocks promotion if AUPRC < threshold
│       ├── mlflow_evaluate_component.py  # mlflow.evaluate() — logs full classifier metrics
│       ├── register_component.py      # Register model, set @production alias
│       └── deploy_component.py        # Canary deployment — health check before promoting stable
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
- **Hyperparameter tuning first** — `RandomizedSearchCV` with `StratifiedKFold` runs before training so the train step always uses the best found parameters.
- **Quality gate** — the `evaluate` step compares AUPRC against a configurable threshold. If the model doesn't clear the bar, the pipeline stops — nothing gets registered or deployed.
- **Scheduled, not drift-gated** — the pipeline runs on a daily cron via KFP recurring runs. Drift detection was removed because a fixed schedule is simpler and predictable for a POC.

### Serving
- **Model loaded at startup** — the FastAPI app pulls `fraud-detector@production` from MLflow Registry when the pod starts. No model files baked into the image.
- **Canary deployment** — the `deploy` pipeline step creates a temporary canary pod that loads the new model. If it becomes Ready within 120s, the stable deployment is rolling-restarted and the canary is deleted. If not, the canary is cleaned up and stable keeps serving the old model.
- **MLflow Tracing** — every `/predict` call is wrapped with `@mlflow.trace`, recording preprocessing and inference as child spans. Traces are visible in the MLflow UI.
- **Prometheus metrics** — `/metrics` endpoint exposes prediction count, fraud detection count, probability distribution, and transaction amount distribution. Scraped every 15s by Prometheus via a `ServiceMonitor`.

### Observability
- **Grafana dashboard** — pre-provisioned at deploy time via a ConfigMap. Shows prediction rate, fraud detection rate, latency percentiles (p50/p95/p99), probability score distribution, transaction amount distribution, error rate, and service health.
- **MLflow Experiment tracking** — every training run logs hyperparameters, AUPRC, F1, ROC-AUC, confusion matrix, and the scaler artifact alongside the model.

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
