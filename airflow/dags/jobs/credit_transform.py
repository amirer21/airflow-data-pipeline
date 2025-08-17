from __future__ import annotations
import os, io, argparse
from typing import Dict
import pandas as pd
import yaml
import boto3
from dotenv import load_dotenv

# 다양한 위치의 .env를 탐색(컨테이너/로컬 모두 대응)
for _path in ("/opt/airflow/config/.env", "config/.env", ".env"):
    if os.path.exists(_path):
        load_dotenv(_path, override=False)

def _load_config(cfg_path: str) -> Dict:
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = os.path.expandvars(f.read())  # ${ENV} 치환
        return yaml.safe_load(raw)

def generate_sample_csv(path: str, n: int = 1000) -> str:
    """로컬 연습용 샘플 신용 데이터 생성"""
    import numpy as np
    rng = np.random.default_rng(42)
    df = pd.DataFrame({
        "customer_id": range(1, n+1),
        "age": rng.integers(20, 70, n),
        "income": rng.normal(5000, 1500, n).clip(1000, 20000),
        "debt": rng.normal(1500, 700, n).clip(0, 20000),
        "default": rng.choice([0,1], size=n, p=[0.9, 0.1])
    })
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    return path

def extract_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path)

def transform_to_silver(df: pd.DataFrame) -> pd.DataFrame:
    # 간단 파생변수 & 리스크 밴딩
    df = df.copy()
    df["debt_to_income"] = (df["debt"] / df["income"]).fillna(0).clip(0, 5)
    df["risk_band"] = pd.cut(
        df["debt_to_income"],
        bins=[-0.01, 0.2, 0.5, 1.0, 5.0],
        labels=["A","B","C","D"]
    )
    return df

def aggregate_to_gold(df_silver: pd.DataFrame) -> pd.DataFrame:
    # 간단한 집계: 밴드별 샘플 수, default율, 평균 소득/부채
    agg = (df_silver
           .groupby("risk_band", dropna=False)
           .agg(
               customers=("customer_id", "count"),
               default_rate=("default", "mean"),
               avg_income=("income", "mean"),
               avg_debt=("debt", "mean"),
           )
           .reset_index())
    return agg

def save_parquet_local(df: pd.DataFrame, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_parquet(path, index=False)
    return path

def upload_parquet_to_s3(df: pd.DataFrame, bucket: str, key: str) -> str:
    s3 = boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT"),
        aws_access_key_id=os.getenv("S3_ACCESS_KEY"),
        aws_secret_access_key=os.getenv("S3_SECRET_KEY"),
        region_name=os.getenv("S3_REGION", "us-east-1"),
    )
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    buf.seek(0)
    s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())
    return f"s3://{bucket}/{key}"

def run_silver(cfg_path: str, to_s3: bool = False) -> Dict:
    cfg = _load_config(cfg_path)
    raw_p = cfg["paths"]["raw"]
    silver_p = cfg["paths"]["silver"]

    if not os.path.exists(raw_p):
        generate_sample_csv(raw_p, n=1000)

    df_raw = extract_csv(raw_p)
    df_silver = transform_to_silver(df_raw)

    if to_s3:
        bucket = cfg["s3"]["bucket"]
        prefix = cfg["s3"]["prefix"].rstrip("/")
        key = f"{prefix}/credit_silver.parquet"
        dest = upload_parquet_to_s3(df_silver, bucket, key)
    else:
        dest = save_parquet_local(df_silver, silver_p)

    return {"stage": "silver", "rows": len(df_silver), "dest": dest}

def run_gold(cfg_path: str, to_s3: bool = False) -> Dict:
    cfg = _load_config(cfg_path)
    silver_p = cfg["paths"]["silver"]
    gold_p = cfg["paths"]["gold"]

    # 로컬 silver에서 읽음(필요 시 S3에서 읽도록 확장 가능)
    df_silver = pd.read_parquet(silver_p)
    df_gold = aggregate_to_gold(df_silver)

    if to_s3:
        bucket = cfg["s3"]["bucket"]
        prefix = cfg["s3"]["prefix"].rstrip("/")
        key = f"{prefix}/credit_gold.parquet"
        dest = upload_parquet_to_s3(df_gold, bucket, key)
    else:
        dest = save_parquet_local(df_gold, gold_p)

    return {"stage": "gold", "rows": len(df_gold), "dest": dest}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--to-s3", action="store_true")
    parser.add_argument("--stage", choices=["silver", "gold"], default="silver")
    args = parser.parse_args()

    if args.stage == "silver":
        print(run_silver(args.config, to_s3=args.to_s3))
    else:
        print(run_gold(args.config, to_s3=args.to_s3))
