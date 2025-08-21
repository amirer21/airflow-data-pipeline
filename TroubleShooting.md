# 이슈 기록 및 해결 방법

1. **워커 → 실행 API 서버 주소 오설정**

   * `execution_api_server_url` 이 `http://localhost:8080/execution/` 또는 `http://airflow-webserver:8080/execution/` 로 잡혀 있었음.
   * 현재는 **webserver를 쓰지 않고 api-server**만 쓰는 구성이라, 워커가 `airflow-webserver` 를 DNS로 못 찾아 `Name or service not known`(httpx `ConnectError`) 발생 → 태스크 시작/상태보고 실패.

2. **MinIO 자격 증명 미설정**

   * `minio_s3` 커넥션을 `s3` 또는 `generic` 타입으로 잘못 생성 → `NoCredentialsError: Unable to locate credentials` 로 `S3Hook` 실패.

3. **풀 없음 경고(선택적)**

   * "Pool default does not exist" 경고가 간헐 발생(초기 설치 직후) → 기본 풀 미존재.

4. **부수 증상**

   * 태스크가 뜨자마자 죽어 **"Error fetching the logs. Try number 0 is invalid."** 같은 로그 수집 오류 노출(실제 원인은 위 1번의 연결 실패).
   * `paramiko.DSSKey` 관련 Provider 경고는 **치명적 아님**(SSH/SFTP 프로바이더 미사용 시 무시 가능).

# 적용한 해결

1. **실행 API 서버 URL을 api-server로 일원화**

   * `docker-compose.yml` 의 공통 env에 아래를 명시하고 모든 Airflow 컴포넌트(특히 `airflow-worker`)에 전달:

     ```
     AIRFLOW__CORE__EXECUTION_API_SERVER_URL=http://airflow-apiserver:8080/execution/
     ```
   * 이후 관련 컨테이너 재생성:

     ```
     docker compose up -d --force-recreate \
       airflow-apiserver airflow-scheduler airflow-worker airflow-triggerer airflow-dag-processor
     ```

2. **MinIO 커넥션을 올바른 타입(aws)로 재생성**

   ```
   docker compose exec airflow-apiserver \
     airflow connections delete minio_s3 || true

   docker compose exec airflow-apiserver \
     airflow connections add minio_s3 \
       --conn-type aws \
       --conn-login minio \
       --conn-password minio12345 \
       --conn-extra '{
         "endpoint_url":"http://minio:9000",
         "region_name":"us-east-1",
         "verify": false,
         "config_kwargs": { "s3": { "addressing_style": "path" } }
       }'
   ```

3. **(선택) 기본 풀 생성**

   ```
   docker compose exec airflow-apiserver \
     airflow pools set default 128 "Default pool"
   ```

4. **DAG 재실행 및 검증**

   * 문제 해결 후 DAG를 트리거 → MinIO(raw/silver/gold)와 Postgres 테이블(`churn_by_state_daily`)까지 정상 적재 확인.

# 상태 확인(상시 점검) 명령어

* **DAG 임포트 에러**

  ```
  docker compose exec airflow-apiserver airflow dags list-import-errors
  ```
* **DAG 존재 여부**

  ```
  docker compose exec airflow-apiserver airflow dags list | grep bronze_silver_gold_incremental
  ```
* **실행 API URL 설정값(워커에서 꼭 확인)**

  ```
  docker compose exec airflow-worker \
    airflow config get-value core execution_api_server_url
  # => http://airflow-apiserver:8080/execution/
  ```
* **컨테이너 DNS 확인(워커 → api-server)**

  ```
  docker compose exec airflow-worker getent hosts airflow-apiserver
  ```
* **워커 → API 서버 HTTP 통신(응답코드만 확인)**

  ```
  docker compose exec airflow-worker bash -lc \
    'curl -sS -o /dev/null -w "%{http_code}\n" http://airflow-apiserver:8080/execution/'
  ```
* **MinIO 커넥션 확인**

  ```
  docker compose exec airflow-apiserver \
    airflow connections get minio_s3 -o json
  # aws_access_key_id / endpoint_url: http://minio:9000 가 보여야 정상
  ```
* **풀 상태**

  ```
  docker compose exec airflow-apiserver \
    airflow pools get default -o yaml
  ```

# 실행/운영 플로우

1. **(옵션) 과거 실패 흔적 정리**

   ```
   docker compose exec airflow-apiserver \
     airflow tasks clear -y bronze_silver_gold_incremental
   ```
2. **언파즈 & 트리거**

   ```
   docker compose exec airflow-apiserver airflow dags unpause bronze_silver_gold_incremental
   docker compose exec airflow-apiserver \
     airflow dags trigger bronze_silver_gold_incremental --conf '{"ds":"2025-08-21"}'
   ```
3. **실행 이력 모니터링 + 워커 로그**

   ```
   docker compose exec airflow-apiserver \
     airflow dags list-runs bronze_silver_gold_incremental -o table

   docker compose logs -f airflow-worker
   ```
4. **산출물 검증**

   ```
   # MinIO 파일
   docker compose exec mc sh -lc \
   "mc ls -r local/raw/churn/    | tail -n 3; echo '---'; \
    mc ls -r local/silver/churn/ | tail -n 3; echo '---'; \
    mc ls -r local/gold/churn/   | tail -n 3"

   # Postgres 적재
   docker compose exec postgres psql -U airflow -d airflow -c \
   "SELECT * FROM churn_by_state_daily ORDER BY ds DESC, state;"
   ```

# 트러블슈팅 메모

* **`Name or service not known`**: 거의 항상 *컨테이너 간 DNS 이름 문제*입니다. 워커에서 `execution_api_server_url` 이 **서비스명:포트**(여기선 `airflow-apiserver:8080`)를 가리키는지 먼저 보세요.
* **`NoCredentialsError`**: Airflow Connection 타입/extra 오설정. `aws` 타입 + `endpoint_url`(MinIO) + key/secret 필수.
* **`paramiko.DSSKey` 경고**: SSH/SFTP 프로바이더의 선택적 경고로 **무시 가능**(미사용 시). 필요하면 해당 프로바이더 버전을 낮추거나 최신 paramiko 호환 버전으로 핀(Pin)하세요.


좋습니다. 아래 블록들을 \*\*기존 문서의 해당 섹션에 "추가"\*\*해 주세요. (번호는 이어서 붙였습니다.)

---

### # 이슈 기록 및 해결 방법 (추가)

**5. Postgres 커넥션 미정의로 `load_gold_to_postgres` 실패**

* 증상

  * 태스크 `load_gold_to_postgres` 실패, 로그에

    ```
    AirflowNotFoundException: The conn_id `dw_postgres` isn't defined
    ```
  * 추가로 Slack 알림 사용 시

    ```
    The conn_id `slack_alert` isn't defined
    ```

    경고/오류 발생.

* 원인

  * DAG 코드가 `postgres_conn_id="dw_postgres"`(및 `slack_alert`)를 참조하지만, **Airflow Connections**에 해당 conn\_id가 등록되어 있지 않음.

---


**5. DW Postgres 커넥션 등록(영구/임시)**

* **권장(영구): 환경변수 방식** — 컨테이너 재시작/재배포에도 유지

  * `.env` 또는 `docker-compose.yml` 의 `environment`에 추가

    ```env
    # DW(Postgres)
    AIRFLOW_CONN_DW_POSTGRES=postgresql+psycopg2://airflow:airflow@postgres:5432/mydatabase

    # (옵션) Slack Webhook — URL 인코딩된 형태 사용
    # 예: https://hooks.slack.com/services/XXX/YYY/ZZZ  →  https%3A%2F%2Fhooks.slack.com%2Fservices%2FXXX%2FYYY%2FZZZ
    AIRFLOW_CONN_SLACK_ALERT=http://:@https%3A%2F%2Fhooks.slack.com%2Fservices%2FXXX%2FYYY%2FZZZ
    ```
  * 반영

    ```bash
    docker compose up -d --force-recreate \
      airflow-apiserver airflow-scheduler airflow-worker airflow-triggerer airflow-dag-processor
    ```

* **수동(임시): CLI 등록** — 빠르게 테스트할 때

  ```bash
  # DW(Postgres)
  docker compose exec airflow-apiserver \
    airflow connections add dw_postgres \
      --conn-type postgres \
      --conn-host postgres \
      --conn-login airflow \
      --conn-password airflow \
      --conn-port 5432 \
      --conn-schema mydatabase

  # (옵션) Slack
  docker compose exec airflow-apiserver \
    airflow connections add slack_alert \
      --conn-type http \
      --conn-host https://hooks.slack.com/services \
      --conn-password '<URL_ENCODED_PATH or token>'
  ```

* **(필수 점검) 대상 DB 존재 확인/생성**

  > `dw_postgres`가 가리키는 DB(`mydatabase`)가 실제로 있어야 합니다.

  ```bash
  docker compose exec postgres psql -U airflow -tc \
    "SELECT 1 FROM pg_database WHERE datname='mydatabase';" \
    | grep -q 1 || \
  docker compose exec postgres psql -U airflow -c "CREATE DATABASE mydatabase;"
  ```

---

### # 상태 확인(상시 점검) 명령어 (추가)

* **DW 커넥션 값 확인**

  ```bash
  docker compose exec airflow-apiserver airflow connections get dw_postgres -o yaml
  ```

* **Postgres 접속성 확인**

  ```bash
  docker compose exec postgres pg_isready -h localhost -p 5432 -U airflow
  ```

* **(선택) Slack 커넥션 확인**

  ```bash
  docker compose exec airflow-apiserver airflow connections get slack_alert -o yaml
  ```

---

### # 실행/운영 플로우 (추가 메모)

* **언파즈/트리거 전에** 아래 2가지를 먼저 확인하면 재실패를 줄일 수 있습니다.

  ```bash
  # 1) DW 커넥션 존재/정합성
  docker compose exec airflow-apiserver airflow connections get dw_postgres -o yaml

  # 2) 대상 DB 존재
  docker compose exec postgres psql -U airflow -tc \
    "SELECT 1 FROM pg_database WHERE datname='mydatabase';"
  ```

> (참고) 기존 산출물 검증에서 Postgres DB 이름을 `airflow`로 사용하셨다면,
> `dw_postgres`를 `mydatabase`로 잡은 현재 구성에 맞춰 **`-d mydatabase`** 로 변경하세요.

```bash
docker compose exec postgres psql -U airflow -d mydatabase -c \
"SELECT * FROM churn_by_state_daily ORDER BY ds DESC, state LIMIT 50;"
```

---

### # 트러블슈팅 메모 (추가)

* \*\*`AirflowNotFoundException: The conn_id \`xxx\` isn't defined`**  
  → *항상* **Admin → Connections** 또는 `AIRFLOW\_CONN\_XXX` 환경변수로 conn_id를 선등록.  
  → DAG에 쓰이는 모든 conn_id(`dw\_postgres`, `minio\_s3`, `slack\_alert\` 등)를 리스트업하고 한 번에 등록·검증.

* **`psycopg2`/권한 에러**
  → 대상 DB/스키마/테이블 권한 확인(`GRANT`), 또는 DB 미생성 여부 점검.
  → 컨테이너 네트워크/서비스명(`postgres`) 기반 DNS가 정확한지 확인.

* **Slack 알림 실패**
  → `slack_alert` conn\_id 누락, URL 인코딩 불일치가 흔한 원인.
  → 보안상 `.env`를 VCS에 커밋하지 말고, 배포 환경에서만 주입하세요.

