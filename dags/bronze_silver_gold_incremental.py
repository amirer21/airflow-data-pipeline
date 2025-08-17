# DAG 보이기/테스트

# 1) 임포트 에러 해소 확인
# docker compose exec airflow-webserver airflow dags list-import-errors

# 2) AirFlow 인식한 DAG 나열
# docker compose exec airflow-webserver airflow dags list | grep bronze_silver_gold_incremental

# 3) DAG 수동 실행(트리거)
# docker compose exec airflow-webserver airflow dags trigger bronze_silver_gold_incremental

# 적재 결과 확인
# docker compose exec postgres bash -lc \
# "psql -U airflow -d airflow -c \"select * from churn_by_state_daily order by ds desc, state;\""


# DAG 실행 이력 보기
# docker compose exec airflow-webserver airflow dags list-runs bronze_silver_gold_incremental

# DAG 켜기 + 즉시 한 번 실행
# docker compose exec airflow-webserver airflow dags unpause bronze_silver_gold_incremental
# docker compose exec airflow-webserver airflow dags trigger  bronze_silver_gold_incremental

# 다시 실행 이력 보기
# docker compose exec airflow-webserver airflow dags list-runs bronze_silver_gold_incremental

  
# dags/bronze_silver_gold_incremental.py
from __future__ import annotations
from datetime import datetime, timedelta
from airflow import DAG
from airflow.decorators import task
from airflow.operators.python import get_current_context
import pandas as pd

# 선택 의존성 가드: pandera 없어도 동작
try:
    import pandera as pa
    HAS_PANDERA = True
except Exception:
    HAS_PANDERA = False

# Slack 웹훅도 선택 사용
try:
    from airflow.providers.slack.hooks.slack_webhook import SlackWebhookHook
    HAS_SLACK = True
except Exception:
    HAS_SLACK = False

S3_CONN_ID = "minio_s3"
PG_CONN_ID = "dw_postgres"
SLACK_CONN_ID = "slack_alert"  # 없으면 무시됨

RAW_BUCKET = "raw"
SILVER_BUCKET = "silver"
GOLD_BUCKET = "gold"

if HAS_PANDERA:
    raw_schema = pa.DataFrameSchema({
        "customer_id": pa.Column(int),
        "state": pa.Column(str),
        "age": pa.Column(int),
        "churned": pa.Column(int),
    })

def slack_alert(context):
    if not HAS_SLACK:
        return
    try:
        dag_id = context["dag"].dag_id
        task_id = context["task_instance"].task_id
        run_id = context["run_id"]
        SlackWebhookHook(slack_webhook_conn_id=SLACK_CONN_ID).send(
            text=f":rotating_light: *Airflow Failed* — `{dag_id}.{task_id}` (run_id={run_id})"
        )
    except Exception as e:
        print("Slack alert error:", e)

default_args = {
    "owner": "demo",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": slack_alert,
}

with DAG(
    dag_id="bronze_silver_gold_incremental",
    start_date=datetime(2024, 1, 1),
    schedule="@daily",
    catchup=False,
    default_args=default_args,
    tags=["bsg", "incremental"],
) as dag:

    @task
    def seed_raw_if_empty() -> int:
        """오늘자 파티션이 비어있으면 샘플 csv를 raw에 생성"""
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook
        import io

        ds = get_current_context()["logical_date"].strftime("%Y-%m-%d")
        key = f"churn/dt={ds}/churn.csv"

        s3 = S3Hook(aws_conn_id=S3_CONN_ID)
        if not s3.check_for_key(key=key, bucket_name=RAW_BUCKET):
            df = pd.DataFrame({
                "customer_id": [1, 2, 3, 4, 5],
                "state": ["CA", "CA", "NY", "TX", "TX"],
                "age": [34, 45, 29, 41, 38],
                "churned": [0, 1, 0, 1, 0],
            })
            s3.load_string(df.to_csv(index=False), key=key, bucket_name=RAW_BUCKET, replace=True)
            return len(df)
        return 0

    @task
    def validate_raw() -> int:
        """스키마 검증(pandera 있으면 사용, 없으면 최소 검증)"""
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook
        import io

        ds = get_current_context()["logical_date"].strftime("%Y-%m-%d")
        key = f"churn/dt={ds}/churn.csv"

        s3 = S3Hook(aws_conn_id=S3_CONN_ID)
        body = s3.read_key(key=key, bucket_name=RAW_BUCKET)
        df = pd.read_csv(io.StringIO(body))

        if HAS_PANDERA:
            raw_schema.validate(df)
        else:
            required = {"customer_id","state","age","churned"}
            assert required <= set(df.columns), "columns mismatch"
            assert df.isna().sum().sum() == 0, "nulls found"
        return len(df)

    @task
    def to_silver() -> int:
        """정제: state 대문자/공백 정리"""
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook
        import io

        ds = get_current_context()["logical_date"].strftime("%Y-%m-%d")
        raw_key = f"churn/dt={ds}/churn.csv"
        silver_key = f"churn/dt={ds}/churn_clean.csv"

        s3 = S3Hook(aws_conn_id=S3_CONN_ID)
        df = pd.read_csv(io.StringIO(s3.read_key(key=raw_key, bucket_name=RAW_BUCKET)))
        df["state"] = df["state"].str.upper().str.strip()

        s3.load_string(df.to_csv(index=False), key=silver_key, bucket_name=SILVER_BUCKET, replace=True)
        return len(df)

    @task
    def to_gold() -> int:
        """집계: 주(state)별 고객수/이탈수"""
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook
        import io

        ds = get_current_context()["logical_date"].strftime("%Y-%m-%d")
        silver_key = f"churn/dt={ds}/churn_clean.csv"
        gold_key = f"churn/dt={ds}/churn_by_state.csv"

        s3 = S3Hook(aws_conn_id=S3_CONN_ID)
        df = pd.read_csv(io.StringIO(s3.read_key(key=silver_key, bucket_name=SILVER_BUCKET)))
        agg = df.groupby("state").agg(customers=("customer_id", "count"),
                                      churned=("churned", "sum")).reset_index()
        s3.load_string(agg.to_csv(index=False), key=gold_key, bucket_name=GOLD_BUCKET, replace=True)
        return len(agg)

    @task
    def load_gold_to_postgres() -> None:
        """(ds,state) PK로 업서트"""
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook
        from airflow.providers.postgres.hooks.postgres import PostgresHook
        import io

        ds = get_current_context()["logical_date"].strftime("%Y-%m-%d")
        gold_key = f"churn/dt={ds}/churn_by_state.csv"

        s3 = S3Hook(aws_conn_id=S3_CONN_ID)
        body = s3.read_key(key=gold_key, bucket_name=GOLD_BUCKET)
        agg = pd.read_csv(io.StringIO(body))
        agg.insert(0, "ds", ds)

        pg = PostgresHook(postgres_conn_id=PG_CONN_ID)
        pg.run("""
            CREATE TABLE IF NOT EXISTS churn_by_state_daily(
              ds DATE NOT NULL,
              state TEXT NOT NULL,
              customers INT,
              churned INT,
              PRIMARY KEY(ds, state)
            );
        """)
        with pg.get_conn() as conn, conn.cursor() as cur:
            for _, r in agg.iterrows():
                cur.execute("""
                    INSERT INTO churn_by_state_daily(ds, state, customers, churned)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (ds, state) DO UPDATE
                      SET customers=EXCLUDED.customers,
                          churned=EXCLUDED.churned;
                """, (r["ds"], r["state"], int(r["customers"]), int(r["churned"])))
            conn.commit()

    seed_raw_if_empty() >> validate_raw() >> to_silver() >> to_gold() >> load_gold_to_postgres()
