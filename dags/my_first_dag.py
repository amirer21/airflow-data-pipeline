from __future__ import annotations

import pendulum

from airflow.models.dag import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="my_first_dag",
    start_date=pendulum.datetime(2025, 1, 1, tz="Asia/Seoul"),
    schedule=None, # 수동으로만 실행
    catchup=False,
    tags=["practice"],
) as dag:
    # BashOperator: 터미널 명령어를 실행하는 오퍼레이터
    hello_task = BashOperator(
        task_id="hello_task",
        bash_command='echo "Hello Airflow! Today is $(date)"',
    )