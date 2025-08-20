````markdown
# Credit DataOps (Airflow + Celery + Redis + Postgres + MinIO)

**로컬 데이터 파이프라인** 템플릿입니다.  
Airflow(CeleryExecutor)로 **Bronze → Silver → Gold → DW(Postgres)** 흐름을 구축하고,
MinIO(S3 호환)로 파일을 관리합니다.

<br>

## 🎯 목적

- Airflow 3.x의 DAG/Task 구성과 **Celery** 기반 분산 실행
- **MinIO(S3)** 를 활용한 원천/정제/집계 데이터 관리(Bronze/Silver/Gold)
- **Postgres** 로 결과 적재(업서트), DBeaver로 조회
- DAG 스케줄/트리거/로그/에러 핸들링 및 **운영 팁**
- (옵션) Slack 알림, Flower 모니터링, Backfill

<br>

## 📦 프로젝트 구조

```text
.
├── docker-compose.yaml              # 메인 스택 정의 (Airflow/Redis/Postgres/MinIO 등)
├── infra/
│   └── docker-compose.override.yml  # (선택) 오버레이
├── dags/
│   ├── hello.py                     # 기본 헬로월드 DAG
│   └── bronze_silver_gold_incremental.py  # 핵심 증분 파이프라인
├── config/
│   ├── airflow.cfg                  # Airflow 설정(마운트)
│   └── config.yaml                  # (선택) 사용자 정의 설정
├── logs/                            # Airflow 로그 볼륨
├── plugins/                         # 커스텀 오퍼레이터/훅/센서 위치
├── minio/                           # MinIO 실제 저장소 볼륨 (raw/silver/gold 생성됨)
│   ├── raw
│   ├── silver
│   └── gold
└── requirements.txt                 # (로컬 개발용) 파이썬 의존성
````

> 참고: `data/raw|silver|gold` 같은 **로컬 폴더는 현재 파이프라인에서 사용하지 않습니다.**
> 실제 파일은 MinIO 볼륨(`./minio`) 아래에 생깁니다.

<br>

## 🚀 설치 & 기동

### (0) 사전 준비

* Docker / Docker Compose
* (권장) Docker Desktop 사용 시 DNS/네트워크 안정적

### (1) `.env` 설정(필수 아님, compose의 `environment:`에 직접 넣어도 됨)

Airflow 3.0.4 + Python 3.12 **constraints** 를 지정하고, 필요한 프로바이더를 추가합니다.

```env
# Airflow 이미지 태그
AIRFLOW_IMAGE_TAG=3.0.4

# Pip constraints (Py 3.12)
PIP_CONSTRAINT=https://raw.githubusercontent.com/apache/airflow/constraints-3.0.4/constraints-3.12.txt

# 부가 패키지(테스트 용도) - 컨테이너 부팅시 설치
# pandera는 네트워크/버전 이슈가 빈번하니 '옵션'으로 권장 (없어도 DAG가 동작하도록 코드 처리됨)
_PIP_ADDITIONAL_REQUIREMENTS=apache-airflow-providers-amazon apache-airflow-providers-postgres pandas apache-airflow-providers-slack
```

> ⚠️ `_PIP_ADDITIONAL_REQUIREMENTS`는 **테스트용 기능**입니다.
> 운영에선 커스텀 이미지를 빌드하세요.

### (2) Docker Compose 주요 포인트

* `airflow-webserver`: `command: webserver` (CeleryExecutor에서 `standalone` 지양)
* `airflow-scheduler`: healthcheck 활성 (8974)
* `postgres`: 호스트 포트 충돌 시 `5433:5432` 매핑 권장
* `minio`: 9000(S3 API), 9001(Console)

> (예시는 기존 저장소의 `docker-compose.yaml`을 그대로 사용하되, 이미지 태그/헬스체크/포트만 점검)

#### 서비스 목록 확인
```sh
docker compose config --services
```

### (3) 스택 기동

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f airflow-init   # 초기화 완료 확인
```

* Airflow UI: [http://localhost:8080](http://localhost:8080) (기본 `airflow` / `airflow`)
* MinIO 콘솔: [http://localhost:9001](http://localhost:9001) (기본 `minio` / `minio12345`)

### (4) Airflow Connections 생성

```bash
docker compose exec airflow-webserver bash -lc '
airflow connections add minio_s3 \
  --conn-type aws \
  --conn-login minio \
  --conn-password minio12345 \
  --conn-extra "{\"endpoint_url\":\"http://minio:9000\",\"region_name\":\"us-east-1\",\"aws_access_key_id\":\"minio\",\"aws_secret_access_key\":\"minio12345\"}"

airflow connections add dw_postgres \
  --conn-type postgres \
  --conn-host postgres \
  --conn-login airflow \
  --conn-password airflow \
  --conn-schema airflow \
  --conn-port 5432
airflow connections list
'
```

### AirFlow 비밀번호 

#### 비밀번호 확인
```
$ docker compose exec airflow-webserver bash -lc '
python - << "PY"
import os, json
p = os.environ.get("AIRFLOW_HOME","/opt/airflow") + "/simple_auth_manager_passwords.json.generated"
with open(p) as f:
    d = json.load(f)
print("username: airflow")
print("password:", d.get("airflow"))
PY
'
```

#### 비밀번호 재설정
```
docker compose exec airflow-webserver bash -lc '
export NEWPW="1234";
python - << "PY"
import os, json
p = os.environ.get("AIRFLOW_HOME","/opt/airflow") + "/simple_auth_manager_passwords.json.generated"
with open(p) as f: d = json.load(f)
d["airflow"] = os.environ["NEWPW"]
with open(p, "w") as f: json.dump(d, f)
print("updated:", p)
PY
'
```

#### 적용
```bash
docker compose restart airflow-webserver
```

> (옵션) Slack 웹훅:

```bash
docker compose exec airflow-webserver airflow connections add slack_alert \
  --conn-type slackwebhook \
  --conn-password "https://hooks.slack.com/services/XXX/YYY/ZZZ"
```

<br>

## 빠른 스모크 테스트: `hello_celery`

`dags/hello.py`

```python
from datetime import datetime
from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="hello_celery",
    start_date=datetime(2024, 1, 1),
    schedule="@once",      # 켜면 한 번 실행
    catchup=False,
    tags=["demo"],
) as dag:
    BashOperator(
        task_id="hello",
        bash_command="echo 'Hello from Airflow!'"
    )
```

실행:

```bash
docker compose exec airflow-webserver airflow dags list | grep hello_celery
docker compose exec airflow-webserver airflow dags unpause hello_celery
docker compose exec airflow-webserver airflow dags trigger  hello_celery
```

UI(Grid/Graph/Log)에서 성공(초록) 확인.

<br>

## 🧩 핵심 DAG: 증분 B→S→G→DW

`dags/bronze_silver_gold_incremental.py`

> **Airflow 3.x 핵심 변경사항 반영**
>
> * `schedule_interval` → `schedule`
> * `get_current_context` 임포트 경로: `from airflow.operators.python import get_current_context`
> * 컨텍스트에서 날짜는 `ds` 대신 **`logical_date`** 사용 (예: `strftime("%Y-%m-%d")`)

```python
from __future__ import annotations
from datetime import datetime, timedelta

from airflow import DAG
from airflow.decorators import task
from airflow.operators.python import get_current_context
import pandas as pd

# 선택 의존성 가드 (pandera 없어도 동작)
try:
    import pandera as pa
    HAS_PANDERA = True
except Exception:
    HAS_PANDERA = False

# Slack도 선택 사용
try:
    from airflow.providers.slack.hooks.slack_webhook import SlackWebhookHook
    HAS_SLACK = True
except Exception:
    HAS_SLACK = False

S3_CONN_ID = "minio_s3"
PG_CONN_ID = "dw_postgres"
SLACK_CONN_ID = "slack_alert"  # 없으면 무시

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
        """(ds,state) PK 업서트"""
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
```

<br>

## ▶️ 실행/검증 명령 모음

### DAG 로딩 오류 확인

```bash
docker compose exec airflow-webserver airflow dags list-import-errors
```

### DAG 인식/상태/실행

```bash
# DAG 인식 확인
docker compose exec airflow-webserver airflow dags list | grep bronze_silver_gold_incremental

# 켜기(언파즈) & 즉시 실행
docker compose exec airflow-webserver airflow dags unpause bronze_silver_gold_incremental
docker compose exec airflow-webserver airflow dags trigger  bronze_silver_gold_incremental

# 실행 이력 보기 (빌드에 따라 두 형식 중 하나가 동작)
docker compose exec airflow-webserver airflow dags list-runs bronze_silver_gold_incremental
# 또는
docker compose exec airflow-webserver airflow dags list-runs --help
```

### 진행 로그

```bash
docker compose logs -f airflow-scheduler
docker compose logs -f airflow-worker
```

### MinIO 산출물 확인

```bash
docker compose exec mc sh -lc \
"mc alias set local http://minio:9000 minio minio12345 >/dev/null; \
 mc ls -r local/raw/churn; mc ls -r local/silver/churn; mc ls -r local/gold/churn"
```

### Postgres 결과 확인

```bash
docker compose exec postgres bash -lc \
"psql -U airflow -d airflow -c \"SELECT * FROM churn_by_state_daily ORDER BY ds DESC, state;\""
```

<br>

## 🧪 DBeaver 접속 (Windows)

`postgres` 서비스에 포트 매핑:

```yaml
ports:
  - "127.0.0.1:5433:5432"   # 로컬 5433 사용(충돌 회피)
```

DBeaver 새 연결:

* Host: `127.0.0.1`
* Port: `5433`
* DB: `airflow`
* User/Pass: `airflow` / `airflow`

<br>

## 🧰 트러블슈팅(자주 겪는 이슈)

* **DAG 파일을 파이썬으로 직접 실행 금지**

  ```bash
  # 잘못된 예
  python dags/my_dag.py  # X
  ```

  Airflow가 DAG을 파싱/실행합니다.

* **ImportError / 파서 에러**

  ```bash
  docker compose exec airflow-webserver airflow dags list-import-errors
  ```

  * Airflow 3.x: `schedule_interval` → `schedule`
  * `get_current_context` 임포트 경로 주의
  * 외부 패키지 Import는 `_PIP_ADDITIONAL_REQUIREMENTS` 또는 커스텀 이미지
  * (본 예제는 `pandera` 없어도 동작하도록 가드)

* **워커가 "ready"만 찍히고 조용함**

  * DAG **Paused 여부** 확인 → `airflow dags unpause <dag>`
  * 실행 이력 확인/트리거 → `airflow dags list-runs …`, `airflow dags trigger …`

* **스케줄러 unhealthy / 버전 충돌**

  * `PIP_CONSTRAINT`(3.12) 지정, 이미지 태그 **통일**
  * 추가 패키지는 constraints 하에서 설치

* **MinIO/PG 연결 문제**

  * Airflow Connections `minio_s3`, `dw_postgres` 재확인
  * MinIO 콘솔에서 버킷/객체 확인

<br>

## 📈 확장 아이디어

* Backfill:

  ```bash
  for d in 2025-08-15 2025-08-16 2025-08-17; do
    docker compose exec airflow-webserver airflow dags test bronze_silver_gold_incremental $d
  done
  ```
* Flower 모니터링:

  ```bash
  docker compose --profile flower up -d flower   # http://localhost:5555
  ```
* 결과 Export:

  ```bash
  docker compose exec postgres bash -lc \
  "psql -U airflow -d airflow -c \"\\COPY churn_by_state_daily TO '/tmp/churn_by_state_daily.csv' CSV HEADER\" && \
   ls -l /tmp/churn_by_state_daily.csv"
  docker cp $(docker compose ps -q postgres):/tmp/churn_by_state_daily.csv ./data/churn_by_state_daily.csv
  ```
* 품질 고도화: Great Expectations/Pandera(네트워크/호환 해결 후 재도입)
* 알림: Slack/Email/Teams

<br>

## 최종 확인 체크리스트

* [ ] UI에서 `bronze_silver_gold_incremental` **ON**
* [ ] MinIO: `raw/silver/gold/churn/dt=YYYY-MM-DD/*.csv` 생성
* [ ] Postgres: `SELECT * FROM churn_by_state_daily;` 결과 확인
* [ ] 워커/스케줄러 로그에서 오류 없음

---

