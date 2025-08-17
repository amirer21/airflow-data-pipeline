# Airflow에서 가장 기본적인 “헬로월드” DAG이고, CeleryExecutor가 제대로 동작하는지 스모크 테스트하려는 목적
# dags/sample.py
from datetime import datetime
from airflow import DAG
from airflow.operators.python import PythonOperator

def say_hello():
    print("Hello from Celery worker!")

with DAG(
    dag_id="hello_celery",
    start_date=datetime(2024, 1, 1),
    schedule="@once",     #  Airflow 3.0: schedule_interval → schedule
    catchup=False,
    tags=["check"],
) as dag:
    hello = PythonOperator(
        task_id="hello_task",
        python_callable=say_hello,
    )
