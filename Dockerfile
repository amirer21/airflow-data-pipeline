# Dockerfile
FROM apache/airflow:3.0.4

# (필요 시 OS 패키지 설치)
USER root
# RUN apt-get update && apt-get install -y --no-install-recommends <packages> && rm -rf /var/lib/apt/lists/*

# pip 작업은 airflow 유저로!
USER airflow

ARG AIRFLOW_VERSION=3.0.4
# 컨테이너의 파이썬 메이저.마이너 버전 추출(예: 3.12)
RUN PY_VER=$(python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')") \
 && pip install --no-cache-dir \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PY_VER}.txt" \
    apache-airflow-providers-amazon \
    apache-airflow-providers-postgres \
    apache-airflow-providers-slack \
    pandas
