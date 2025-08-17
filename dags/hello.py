from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime

with DAG("hello", start_date=datetime(2025,1,1), schedule=None, catchup=False, tags=["check"]):
    BashOperator(task_id="say_hi", bash_command="echo hello from airflow")
