from __future__ import annotations
from datetime import datetime, timedelta
from airflow.decorators import dag, task
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.postgres.hooks.postgres import PostgresHook
import io
import pandas as pd

S3_CONN_ID = "minio_s3"
PG_CONN_ID = "dw_postgres"

RAW_BUCKET = "raw"
SILVER_BUCKET = "silver"
GOLD_BUCKET = "gold"

RAW_KEY = "churn/churn.csv"
SILVER_KEY = "churn/churn_clean.csv"
GOLD_KEY = "churn/churn_by_state.csv"

default_args = {
    "owner": "demo",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

@dag(
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["demo", "bronze-silver-gold"],
)
def bronze_silver_gold():
    """Bronze→Silver→Gold 변환 후 DW(Postgres)에 적재하는 연습 DAG"""

    @task
    def seed_raw_to_minio():
        """로컬에서 간단한 CSV를 만들어 raw 버킷에 업로드"""
        df = pd.DataFrame({
            "customer_id": [1, 2, 3, 4, 5],
            "state": ["CA", "CA", "NY", "TX", "TX"],
            "age": [34, 45, 29, 41, 38],
            "churned": [0, 1, 0, 1, 0],
        })
        csv_bytes = df.to_csv(index=False).encode()
        hook = S3Hook(aws_conn_id=S3_CONN_ID)
        hook.load_bytes(bytes_data=csv_bytes, key=RAW_KEY, bucket_name=RAW_BUCKET, replace=True)
        return len(df)

    @task
    def validate_raw():
        """raw csv 컬럼/결측치 간단 검증"""
        hook = S3Hook(aws_conn_id=S3_CONN_ID)
        body = hook.read_key(key=RAW_KEY, bucket_name=RAW_BUCKET)
        df = pd.read_csv(io.StringIO(body))
        # 아주 간단한 체크 (실무에서는 Great Expectations/Pandera 권장)
        assert {"customer_id", "state", "age", "churned"} <= set(df.columns), "columns mismatch"
        assert df.isna().sum().sum() == 0, "nulls found"
        return df.shape[0]

    @task
    def to_silver():
        """간단한 정제: state 대문자화 등 → silver csv 저장"""
        s3 = S3Hook(aws_conn_id=S3_CONN_ID)
        raw = s3.read_key(key=RAW_KEY, bucket_name=RAW_BUCKET)
        df = pd.read_csv(io.StringIO(raw))
        df["state"] = df["state"].str.upper().str.strip()
        csv_bytes = df.to_csv(index=False).encode()
        s3.load_bytes(bytes_data=csv_bytes, key=SILVER_KEY, bucket_name=SILVER_BUCKET, replace=True)
        return len(df)

    @task
    def to_gold():
        """집계: 주(state)별 고객수/이탈수 → gold csv 저장"""
        s3 = S3Hook(aws_conn_id=S3_CONN_ID)
        silver = s3.read_key(key=SILVER_KEY, bucket_name=SILVER_BUCKET)
        df = pd.read_csv(io.StringIO(silver))
        agg = df.groupby("state").agg(customers=("customer_id", "count"),
                                      churned=("churned", "sum")).reset_index()
        csv_bytes = agg.to_csv(index=False).encode()
        s3.load_bytes(bytes_data=csv_bytes, key=GOLD_KEY, bucket_name=GOLD_BUCKET, replace=True)
        return agg.to_dict(orient="records")

    @task
    def load_gold_to_postgres():
        """Gold 결과를 Postgres 테이블로 적재 (간단히 truncate→copy)"""
        s3 = S3Hook(aws_conn_id=S3_CONN_ID)
        body = s3.read_key(key=GOLD_KEY, bucket_name=GOLD_BUCKET)
        df = pd.read_csv(io.StringIO(body))

        pg = PostgresHook(postgres_conn_id=PG_CONN_ID)
        pg.run("""
            CREATE TABLE IF NOT EXISTS churn_by_state (
              state TEXT PRIMARY KEY,
              customers INT,
              churned INT
            );
        """)
        pg.run("TRUNCATE TABLE churn_by_state;")

        csv_no_header = df.to_csv(index=False, header=False)
        conn = pg.get_conn()
        cur = conn.cursor()
        cur.copy_expert(
            "COPY churn_by_state(state, customers, churned) FROM STDIN WITH CSV",
            io.StringIO(csv_no_header),
        )
        conn.commit()

    n = seed_raw_to_minio()
    v = validate_raw()
    s = to_silver()
    g = to_gold()
    load_gold_to_postgres().set_upstream(g)

bronze_silver_gold()
