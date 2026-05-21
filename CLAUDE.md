# CLAUDE.md — MLOps Project Reference

This file is read by Claude Code at the start of every session. It contains project conventions, known issues, operational runbooks, and context needed to work effectively on this codebase.

---

## Project Summary

End-to-end MLOps platform for real-time credit card fraud detection on AWS EKS. Fully automated training pipeline (Kubeflow Pipelines), MLflow model registry, KServe serving, and Prometheus/Grafana observability — all behind a single NGINX LoadBalancer.

**Primary AWS Account:** 853973692277  
**Cluster Name:** mlops-cluster  
**Region:** us-east-1  
**AWS Profile:** `limited-admin-853973692277`

---

## AWS Authentication

SSO sessions expire regularly. Always refresh before any kubectl or terraform work:

```
aws sso login --profile limited-admin-853973692277
aws eks update-kubeconfig --name mlops-cluster --region us-east-1 --profile limited-admin-853973692277
```

If kubectl commands silently return no output (instead of an error), the SSO session has expired.

---

## Cluster Entry Points

Everything routes through **one NGINX LoadBalancer**:

| Service | Path |
|---|---|
| Kubeflow Dashboard | `http://<nginx-lb>/` |
| KFP UI | `http://<nginx-lb>/pipeline/` |
| Jupyter Web App | `http://<nginx-lb>/jupyter/` |
| KServe Models | `http://<nginx-lb>/kserve-endpoints/` |
| Fraud detector UI | `http://<nginx-lb>/fraud-detector/` |
| Predict endpoint | `http://<nginx-lb>/predict` |

Get the NGINX LB hostname:
```
kubectl get svc -n ingress-nginx ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

**Separate LBs** (still exist, to be consolidated):
- JupyterHub: `proxy-public` in `jhub` namespace
- MLflow: `mlflow` service in `mlflow` namespace (port 5000)
- Grafana: `kube-prometheus-stack-grafana` in `monitoring` namespace

---

## Docker Images

Both images **must be built for `linux/amd64`**. The EKS nodes are x86_64. Building on Apple Silicon without `--platform linux/amd64` produces ARM64 images that crash with `exec format error`.

```
docker buildx build --platform linux/amd64 -t srikanthkarthi/mlops-serving:latest --push 4_serving/
docker buildx build --platform linux/amd64 -t srikanthkarthi/mlops-pipeline-base:latest --push 5_pipeline/
```

Never use plain `docker build` on this project.

---

## Pipeline Operations

**Compile + upload + trigger a run:**
```
cd 5_pipeline
python pipeline.py                # compiles to compiled/pipeline.yaml
python upload_pipeline.py         # uploads new version + starts run
```

**Trigger only (no new version):**
```
python trigger_run.py
MODEL_TYPE=xgb python trigger_run.py
```

**Schedule daily runs (02:00 UTC):**
```
python schedule_pipeline.py
```

**KFP endpoint** is hardcoded in `upload_pipeline.py` and `trigger_run.py`:
`http://a9874180177eb41338621ad756a39fb9-576280531.us-east-1.elb.amazonaws.com`

---

## Known Recurring Issues and Fixes

### 1. MLMD "Cannot get MLMD objects from Metadata store" error in KFP UI

**Cause:** `metadata-writer` replays all historical pod events on restart → MySQL InnoDB deadlocks.

**Fix (run in order):**
```
# Step 1 — delete all old completed/failed/evicted pipeline pods
kubectl delete pods -n kubeflow --field-selector=status.phase==Succeeded
kubectl delete pods -n kubeflow --field-selector=status.phase==Failed
kubectl get pods -n kubeflow --no-headers | grep -v Running | awk '{print $1}' | xargs kubectl delete pod -n kubeflow --force --grace-period=0

# Step 2 — delete old Argo workflow objects
kubectl get workflows -n kubeflow --no-headers | grep -v Running | awk '{print $1}' | xargs kubectl delete workflow -n kubeflow

# Step 3 — restart metadata stack
kubectl rollout restart deployment/metadata-writer deployment/metadata-grpc-deployment deployment/metadata-envoy-deployment deployment/ml-pipeline -n kubeflow
```

Run this cleanup regularly to prevent accumulation.

### 2. KServe InferenceService external URL shows `fraud-detector-user.example.com`

**Cause:** KServe controller sets status URL from its `ingressDomain` config (`example.com`). Patch wears off on reconcile.

**Permanent fix:** `deploy_component.py` patches the status URL after every successful deploy (reads NGINX LB from `ingress-nginx` namespace). If it resets, patch manually:
```
kubectl patch inferenceservice fraud-detector -n user --subresource=status --type=merge \
  -p '{"status":{"url":"http://<nginx-lb-hostname>/fraud-detector"}}'
```

### 3. Pipeline pod evicted — "node was low on resource: ephemeral-storage"

**Cause:** Training step generates large container logs that fill up the 20GB node disk.

**Immediate fix:** Clean up old pods (see issue #1). Old `/var/log/pods` entries are freed when pods are deleted.

**Permanent fix:** Node disk size increased to 50GB (general) and 80GB (ml-training) in `terraform/eks/eks.tf` via `block_device_mappings`. Apply with `terraform apply` (causes rolling node replacement).

### 4. `exec format error` on pipeline or serving pods

**Cause:** Docker image built on Apple Silicon (ARM64) but EKS nodes are x86_64.

**Fix:** Rebuild with `--platform linux/amd64` (see Docker Images section above).

### 5. Terraform "context canceled" error on node group update

**Cause:** Terraform's default wait timeout is shorter than the EKS rolling node replacement. The update continues in AWS after Terraform exits.

**Fix:** Check node group status. When it returns `ACTIVE`, re-run `terraform apply`:
```
aws eks describe-nodegroup --cluster-name mlops-cluster \
  --nodegroup-name general-20260505082734383300000010 \
  --profile limited-admin-853973692277 --region us-east-1 \
  --query 'nodegroup.status' --output text
```

### 6. Terraform "ResourceInUseException: Nodegroup cannot be updated as it is currently not in Active State"

**Cause:** Terraform apply run before a previous node group update finished.

**Fix:** Wait for status to be `ACTIVE` (see issue #5), then re-run `terraform apply`.

### 7. Pipeline logs show "unable to retrieve container logs for containerd://..."

**Cause:** Container runtime GC'd the log buffer after pod completed. Logs ARE saved to MinIO.

**Where to find logs:** In the KFP UI step panel, check the **Artifacts** section → `executor-logs` artifact (MinIO-backed). The Logs tab uses the live K8s API which fails after pod deletion.

If executor-logs are also missing (pod was evicted before KFP launcher could write them), the only recovery is to re-run the pipeline.

---

## Namespace Reference

| Namespace | Contents |
|---|---|
| `kubeflow` | Pipelines, Dashboard, Notebooks, KServe controller, notebook proxy |
| `mlflow` | MLflow tracking server |
| `kserve` | KServe Models Web App |
| `user` | User profile: notebooks, InferenceServices, pipeline job pods |
| `jhub` | JupyterHub |
| `monitoring` | Prometheus, Grafana |
| `cert-manager` | cert-manager controller |
| `ingress-nginx` | NGINX ingress controller |

---

## Terraform

All infra is in `terraform/eks/`. Key files:

- `eks.tf` — EKS cluster + node groups (disk sizes via `block_device_mappings`)
- `ingress.tf` — cert-manager, KServe, all NGINX ingress rules, fraud-detector ingresses
- `serving.tf` — RBAC for pipeline-runner to manage InferenceServices
- `notebooks.tf` — Kubeflow notebooks, dashboard, JupyterHub, profiles, notebook proxy
- `monitoring.tf` — kube-prometheus-stack, ServiceMonitor, Grafana dashboard
- `mlflow.tf` — MLflow Helm chart

**Always use profile `limited-admin-853973692277`** in `terraform.tfvars` (`aws_profile` variable).

Node disk sizes (set in `eks.tf`):
- General nodes: 50 GB gp3
- ML-training nodes: 80 GB gp3

---

## Kubeflow — No Istio Design

This cluster runs Kubeflow **without Istio**. Key workarounds in place:

- `stub-authorization-policy-crd.yaml` — stub CRD satisfies the profiles controller's Istio watch
- `notebook-proxy` deployment in `kubeflow` namespace — nginx proxy that routes `/notebook/{ns}/{name}/` to the correct notebook service (replaces Istio VirtualService routing)
- KServe runs in `RawDeployment` mode (no Knative, no Istio VirtualServices)
- KServe Models Web App kustomization removes the Istio VirtualService resource

---

## Model & Pipeline Details

- **Model:** Random Forest (default), XGBoost, LightGBM, or Logistic Regression
- **Dataset:** `s3://mlops-cluster-mlflow-853973692277/data/creditcard.csv`
- **MLflow tracking:** `http://mlflow.mlflow.svc.cluster.local:5000` (internal)
- **Model registry name:** `fraud-detector`
- **Production alias:** `@production`
- **Quality gate:** AUPRC ≥ 0.75
- **Canary timeout:** 120 seconds (configurable via `canary_wait_seconds` param)
- **Serving image:** `srikanthkarthi/mlops-serving:latest`
- **Pipeline base image:** `srikanthkarthi/mlops-pipeline-base:latest`

---

## Routine Cluster Maintenance

Run this after every batch of pipeline runs to prevent disk pressure and MLMD deadlocks:

```
# Delete all completed/failed pods across all namespaces
kubectl delete pods -A --field-selector=status.phase==Succeeded
kubectl delete pods -A --field-selector=status.phase==Failed

# Delete old Argo workflows in kubeflow namespace
kubectl get workflows -n kubeflow --no-headers | grep -v Running | awk '{print $1}' | xargs kubectl delete workflow -n kubeflow 2>/dev/null
```
