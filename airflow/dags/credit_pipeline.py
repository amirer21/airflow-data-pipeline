from datetime import datetime
from airflow import DAG
from airflow.operators.python import PythonOperator

# jobs 패키지에서 함수 재사용
from jobs.credit_transform import run_silver, run_gold

DEFAULT_ARGS = {
    "owner": "demo",
    "depends_on_past": False,
    "retries": 0,
}

with DAG(
    dag_id="credit_pipeline",
    start_date=datetime(2024, 1, 1),
    schedule_interval=None,     # 필요시 "0 * * * *" 등으로 변경
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=["credit", "minio", "demo"],
) as dag:

    def _silver_local():
        print(run_silver("config/config.yaml", to_s3=False))

    def _silver_to_s3():
        print(run_silver("config/config.yaml", to_s3=True))

    def _gold_local():
        print(run_gold("config/config.yaml", to_s3=False))

    silver_local = PythonOperator(
        task_id="silver_local",
        python_callable=_silver_local
    )

    silver_to_s3 = PythonOperator(
        task_id="silver_to_s3",
        python_callable=_silver_to_s3
    )

    gold_local = PythonOperator(
        task_id="gold_local",
        python_callable=_gold_local
    )

    silver_local >> silver_to_s3 >> gold_local
