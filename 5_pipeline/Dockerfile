FROM python:3.11-slim

RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    boto3==1.38.0 \
    imbalanced-learn==0.14.1 \
    joblib==1.5.3 \
    lightgbm==4.6.0 \
    mlflow==3.11.1 \
    numpy==2.4.4 \
    pandas==2.3.3 \
    scikit-learn==1.8.0 \
    scipy==1.15.3 \
    xgboost==3.0.1
