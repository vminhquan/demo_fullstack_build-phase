# 09 — Docker và kết nối giữa services

## 1. Containers của Luồng B

```text
frontend
backend
postgres-pgvector
redis
minio
minio-init (one-shot tạo bucket)
migrate (one-shot Alembic upgrade)
```

Worker/CARLA/ScenarioRunner là Luồng A, không nằm trong Compose của Luồng B.

## 2. Docker Compose skeleton

```yaml
services:
  frontend:
    build: ./frontend
    environment:
      API_INTERNAL_URL: http://backend:8000
    ports:
      - "3000:3000"
    depends_on:
      backend:
        condition: service_healthy

  backend:
    build: ./backend
    environment:
      DATABASE_URL: postgresql+asyncpg://scenario:${DB_PASSWORD}@postgres:5432/scenario_forge
      REDIS_URL: redis://redis:6379/0
      JWT_SECRET: ${JWT_SECRET}

      MINIO_ENDPOINT: minio:9000
      MINIO_ACCESS_KEY: ${MINIO_ACCESS_KEY}
      MINIO_SECRET_KEY: ${MINIO_SECRET_KEY}
      MINIO_SECURE: "false"
      MINIO_BUCKET_ARTIFACTS: scenario-forge-artifacts

      EMBEDDING_PROVIDER: ${EMBEDDING_PROVIDER:-disabled}
      EMBEDDING_MODEL: ${EMBEDDING_MODEL:-}
      EMBEDDING_DIM: ${EMBEDDING_DIM:-768}
    expose:
      - "8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
      minio-init:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health/ready"]
      interval: 10s
      timeout: 3s
      retries: 10

  postgres:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_DB: scenario_forge
      POSTGRES_USER: scenario
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U scenario -d scenario_forge"]
      interval: 5s
      timeout: 3s
      retries: 20

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redis_data:/data

  minio:
    image: quay.io/minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ACCESS_KEY}
      MINIO_ROOT_PASSWORD: ${MINIO_SECRET_KEY}
    volumes:
      - minio_data:/data
    expose:
      - "9000"        # S3 API trong Docker network
    ports:
      - "9001:9001"   # dev console; production không public trực tiếp
    restart: unless-stopped

  minio-init:
    image: quay.io/minio/mc
    depends_on:
      minio:
        condition: service_started
    environment:
      MINIO_ACCESS_KEY: ${MINIO_ACCESS_KEY}
      MINIO_SECRET_KEY: ${MINIO_SECRET_KEY}
    entrypoint: >
      /bin/sh -c "
      until mc alias set local http://minio:9000 $$MINIO_ACCESS_KEY $$MINIO_SECRET_KEY; do
        echo 'waiting for minio';
        sleep 2;
      done;
      mc mb --ignore-existing local/scenario-forge-artifacts;
      exit 0;
      "
    restart: "no"

  migrate:
    build: ./backend
    command: alembic upgrade head
    environment:
      DATABASE_URL: postgresql+asyncpg://scenario:${DB_PASSWORD}@postgres:5432/scenario_forge
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"

volumes:
  postgres_data:
  redis_data:
  minio_data:
```

> Đây là skeleton cho dev. CI/production nên pin version hoặc digest của image đã kiểm thử.

## 3. PostgreSQL extensions

```sql
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS vector;
```

`vector` chỉ phục vụ semantic search; dữ liệu nghiệp vụ vẫn nằm trong PostgreSQL bình thường.

## 4. MinIO ports và network

```text
9000 = S3-compatible API
9001 = MinIO Console
```

Trong Docker network:

```text
backend -> http://minio:9000
minio-init -> http://minio:9000
```

MinIO data persist ở:

```text
minio_data -> /data
```

Development có thể publish `9001` để mở console. Không cần publish `9000` nếu Browser chỉ upload/download qua FastAPI.

Nếu sau này dùng presigned URL trực tiếp từ Browser/Worker ngoài Docker network, phải expose MinIO qua một hostname/gateway mà client truy cập được và ký URL bằng hostname đó. Không dùng `minio:9000` trong presigned URL gửi cho browser bên ngoài Docker.

## 5. Network rules

MVP:

```text
host:3000 -> frontend
frontend -> backend
backend -> postgres
backend -> redis
backend -> minio:9000
host:9001 -> MinIO Console (dev only)
```

Không publish PostgreSQL/Redis ra Internet.

## 6. Next.js proxy

```ts
const nextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.API_INTERNAL_URL}/api/:path*`,
      },
    ];
  },
};
```

MVP: Browser upload/download file qua FastAPI. FastAPI authorize rồi stream tới/từ MinIO.

Phase sau: presigned PUT/GET cho video/log lớn, sau khi có `MINIO_PUBLIC_BASE_URL` hoặc reverse proxy/gateway phù hợp.

## 7. Backend storage config

Composition root tạo adapter:

```python
storage: StoragePort = MinioStorageAdapter(
    endpoint=settings.MINIO_ENDPOINT,
    access_key=settings.MINIO_ACCESS_KEY,
    secret_key=settings.MINIO_SECRET_KEY,
    bucket=settings.MINIO_BUCKET_ARTIFACTS,
    secure=settings.MINIO_SECURE,
)
```

Domain/Application không import MinIO SDK.

Backend có thể verify bucket tồn tại khi startup/readiness, nhưng việc tạo bucket chính do `minio-init` đảm nhiệm để startup responsibility rõ ràng.

## 8. Worker WebSocket — verify ở Luồng A

Compose Luồng B **không chạy Worker**.

```text
Worker Luồng A
    -> outbound WSS
    -> FastAPI integration endpoint
```

Các message cần thống nhất:

```text
worker.hello
worker.heartbeat
job.assigned
job.started
job.progress
job.completed
job.failed
```

Artifact flow mặc định:

```text
Worker -> FastAPI -> MinIO
```

Khi cần tối ưu file lớn:

```text
Worker -> FastAPI xin upload intent
FastAPI -> Worker presigned PUT
Worker -> MinIO trực tiếp
Worker -> FastAPI finalize
```

Phần lifecycle WebSocket/CARLA vẫn do Luồng A verify và implement.

## 9. Embedding provider

Không bắt buộc thêm container mới. FastAPI phụ thuộc `EmbeddingPort`; khi disabled hệ thống vẫn chạy structured/keyword search.

## 10. Config/secrets

Không commit:

```text
DB_PASSWORD
JWT_SECRET
MINIO_ACCESS_KEY
MINIO_SECRET_KEY
WORKER_SERVICE_TOKEN
EMBEDDING_API_KEY
```

`.env.example` chỉ chứa tên biến và placeholder.

Compose trên dùng credential MinIO root cho Backend để **dev đơn giản**. Production nên tạo service credential/policy riêng cho Backend với quyền tối thiểu trên bucket/prefix cần thiết.

Ví dụ `.env.example`:

```dotenv
DB_PASSWORD=change-me
JWT_SECRET=change-me
MINIO_ACCESS_KEY=scenario-dev
MINIO_SECRET_KEY=change-me-minimum-strong-secret
EMBEDDING_PROVIDER=disabled
EMBEDDING_MODEL=
EMBEDDING_DIM=768
```

## 11. Health/readiness

```text
GET /health/live
GET /health/ready
```

`/health/ready` tối thiểu kiểm tra PostgreSQL. Với endpoint cần artifact, Backend phải detect MinIO unavailable và trả lỗi storage rõ ràng. Nếu muốn strict readiness cho toàn hệ thống, thêm `stat/bucket_exists` check MinIO vào readiness.
