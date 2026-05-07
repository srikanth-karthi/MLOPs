from kfp import dsl


@dsl.component(base_image="srikanthkarthi/mlops-pipeline-base:latest")
def tune(
    s3_bucket: str,
    aws_region: str,
    s3_data_key: str = "data/creditcard.csv",
    model_type: str = "rf",
    n_iter: int = 10,
    n_estimators_options: str = "50,100,150,200",
    max_depth_options: str = "10,20,30",
) -> str:
    import json
    import os

    import boto3
    import pandas as pd
    from imblearn.over_sampling import SMOTE
    from sklearn.model_selection import RandomizedSearchCV, StratifiedKFold
    from sklearn.preprocessing import StandardScaler

    os.environ["AWS_DEFAULT_REGION"] = aws_region
    s3 = boto3.client("s3", region_name=aws_region)
    print(f"Downloading s3://{s3_bucket}/{s3_data_key}")
    s3.download_file(s3_bucket, s3_data_key, "/tmp/data.csv")

    df = pd.read_csv("/tmp/data.csv").sort_values("Time")
    X = df.drop("Class", axis=1)
    y = df["Class"]
    split = int(len(df) * 0.8)
    X_train, y_train = X.iloc[:split].copy(), y.iloc[:split]

    scaler = StandardScaler()
    X_train[["Amount", "Time"]] = scaler.fit_transform(X_train[["Amount", "Time"]])
    X_res, y_res = SMOTE(random_state=42).fit_resample(X_train, y_train)

    if model_type == "rf":
        from sklearn.ensemble import RandomForestClassifier
        ne_opts = [int(x) for x in n_estimators_options.split(",")]
        md_opts = [int(x) for x in max_depth_options.split(",")] + [None]
        estimator = RandomForestClassifier(random_state=42)
        param_dist = {
            "n_estimators":      ne_opts,
            "max_depth":         md_opts,
            "min_samples_split": [2, 5, 10],
            "min_samples_leaf":  [1, 2, 4],
            "class_weight":      ["balanced"],
        }
    elif model_type == "xgb":
        from xgboost import XGBClassifier
        estimator = XGBClassifier(random_state=42, eval_metric="aucpr", use_label_encoder=False)
        param_dist = {
            "n_estimators": [int(x) for x in n_estimators_options.split(",")],
            "max_depth":    [int(x) for x in max_depth_options.split(",")],
            "learning_rate": [0.01, 0.05, 0.1, 0.2],
            "subsample":     [0.6, 0.8, 1.0],
        }
    elif model_type == "lgbm":
        from lightgbm import LGBMClassifier
        estimator = LGBMClassifier(random_state=42, class_weight="balanced")
        param_dist = {
            "n_estimators":  [int(x) for x in n_estimators_options.split(",")],
            "max_depth":     [int(x) for x in max_depth_options.split(",")],
            "learning_rate": [0.01, 0.05, 0.1, 0.2],
            "num_leaves":    [31, 63, 127],
        }
    elif model_type == "lr":
        from sklearn.linear_model import LogisticRegression
        estimator = LogisticRegression(max_iter=1000, solver="lbfgs", class_weight="balanced", random_state=42)
        param_dist = {
            "C":       [0.001, 0.01, 0.1, 1.0, 10.0],
            "penalty": ["l2"],
        }
    else:
        raise ValueError(f"Unknown model_type '{model_type}'. Choose: rf, xgb, lgbm, lr")

    search = RandomizedSearchCV(
        estimator,
        param_dist,
        n_iter=min(n_iter, len(param_dist.get("C", [n_iter]))),
        scoring="average_precision",
        cv=StratifiedKFold(n_splits=3, shuffle=True, random_state=42),
        n_jobs=-1,
        random_state=42,
        verbose=1,
    )
    search.fit(X_res, y_res)

    best = search.best_params_
    print(f"Best params: {best}")
    print(f"Best CV AUPRC: {search.best_score_:.4f}")
    return json.dumps(best)
