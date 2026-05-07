# MLOps Implementation Plan: MLflow + Kubeflow on EKS

## Overview

End-to-end MLOps pipeline for credit card fraud detection:
data → training → experiment tracking → model registry → Kubeflow pipeline → serving on EKS.

**Stack:** AWS EKS · MLflow 3.7.0 · Kubeflow Pipelines 2.14.4 · S3 · Docker Hub · FastAPI

---

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Exploratory notebook | ✅ Done |
| 2 | Production training script + Docker image | ✅ Done |
| 3 | MLflow on EKS (Helm) | ✅ Done |
| 4 | Training Job on EKS | ✅ Done |
| 5 | EBS CSI driver + StorageClass | ✅ Done |
| 6 | Kubeflow Pipelines 2.14.4 on EKS | ✅ Done |
| 7 | MLflow Model Registry | ✅ Done |
| 8 | Kubeflow pipeline (preprocess → train → evaluate → register) | ✅ Done |
| 9 | FastAPI serving app + Docker image | ✅ Done |
| 10 | Serving fleet on EKS (Deployment + HPA) | ✅ Done |

---

## Project Structure

```
Mlops/
├── MLOPS_IMPLEMENTATION_PLAN.md
├── data/
│   └── creditcard.csv                  # Raw dataset (gitignored)
│
├── 1_notebook/
│   └── train_model.ipynb               # Exploratory training
│
├── 2_training/
│   ├── train.py                        # ✅ Production training script
│   ├── Dockerfile                      # ✅ linux/amd64 image → Docker Hub
│   └── requirements.txt               # ✅ mlflow, sklearn, boto3, imbalanced-learn
│
├── 3_registry/
│   └── register_model.py              # ✅ fraud-detector v1 @production
│
├── 4_serving/
│   ├── app.py                          # 🔲 FastAPI /health + /predict
│   ├── Dockerfile                      # 🔲 Serving container
│   └── requirements.txt               # 🔲 fastapi, uvicorn, mlflow, boto3
│
├── 5_pipeline/
│   ├── pipeline.py                     # 🔲 @dsl.pipeline wiring all steps
│   ├── components/
│   │   ├── preprocess_component.py    # 🔲 @dsl.component
│   │   ├── train_component.py         # 🔲 @dsl.component
│   │   ├── evaluate_component.py      # 🔲 @dsl.component (quality gate)
│   │   └── register_component.py      # 🔲 @dsl.component
│   ├── compiled/
│   │   └── pipeline.yaml              # 🔲 Compiled Argo workflow YAML
│   └── upload_pipeline.py             # 🔲 Upload + trigger via KFP client
│
├── 6_k8s/
│   ├── training-job.yaml              # ✅ Batch training Job
│   ├── deployment.yaml                # 🔲 Serving Deployment (RollingUpdate)
│   ├── service.yaml                   # 🔲 LoadBalancer service
│   └── hpa.yaml                       # 🔲 HorizontalPodAutoscaler
│
└── terraform/eks/
    ├── eks.tf                          # ✅ EKS cluster + node groups
    ├── vpc.tf                          # ✅ VPC + subnets
    ├── s3.tf                           # ✅ S3 bucket + IRSA for MLflow
    ├── mlflow.tf                       # ✅ MLflow Helm release
    ├── ebs-csi.tf                      # ✅ EBS CSI driver + gp3 StorageClass
    ├── kubeflow.tf                     # ✅ KFP 2.14.4 + LoadBalancer UI + TLS secret
    └── training-job.tf                 # ✅ Kubernetes training Job
```

---

## Infrastructure

| Resource | Details |
|----------|---------|
| Cluster | EKS 1.33, `mlops-cluster`, `us-east-1` |
| Nodes | `t3.large` general (1–6) + `t3.large` ml-training (0–5) |
| MLflow | community-charts v1.8.1 (MLflow 3.7.0), SQLite backend, S3 artifacts |
| Artifact store | `s3://mlops-cluster-mlflow-853973692277` |
| KFP | Standalone 2.14.4, MinIO + MySQL backends |
| Container registry | Docker Hub (`srikanthkarthi/mlops-training:latest`) |
| StorageClass | `gp3` (default), EBS CSI driver |

---

## Completed Training Run

| Metric | Value |
|--------|-------|
| Run ID | `003db4126f834bcaa255e1beefbf85e1` |
| Model | RandomForest, 100 estimators |
| AUPRC | 0.8281 |
| ROC-AUC | 0.9805 |
| Fraud F1 | 0.8507 |
| Fraud Recall | 0.7600 |
| Fraud Precision | 0.9661 |

---

## Phase 7 — Model Registry (`3_registry/register_model.py`)

Connect to MLflow, find best run by AUPRC, validate threshold, register as `fraud-detector`, transition None → Staging → Production.

```bash
MLFLOW_TRACKING_URI=http://<elb> python 3_registry/register_model.py
```

---

## Phase 8 — Kubeflow Pipeline (`5_pipeline/`)

Four `@dsl.component` steps wired by `@dsl.pipeline`:

```
preprocess → train → evaluate (gate: AUPRC ≥ 0.75) → register
```

Compile to YAML, upload to KFP, trigger run with parameter overrides.

---

## Phase 9 — FastAPI Serving (`4_serving/`)

- `GET /health` — liveness/readiness probe
- `POST /predict` — loads `models:/fraud-detector/Production` from MLflow at startup
- Model pulled from S3 via MLflow Registry URI — no weights baked into image

---

## Phase 10 — Serving Fleet (`6_k8s/`)

- Deployment: 2 replicas, `RollingUpdate` (maxSurge=1, maxUnavailable=0)
- HPA: 2–10 pods, target 60% CPU
- Readiness probe on `/health` prevents traffic until model is loaded from S3
