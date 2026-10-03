# Scenario Forge — Local Docker stack

Compose giai đoạn hiện tại chạy Next.js frontend, FastAPI backend, PostgreSQL 17 + pgvector và Redis. CARLA/ScenarioRunner/Worker Luồng A và MinIO sẽ được thêm lại khi cần upload XOSC/log/video.

## Chuẩn bị cấu hình

```bash
cd /Users/minhquanvo/Documents/demo_fullstack_build-phase
cp .env.example .env
```

Trong `.env`, thay ít nhất các giá trị `change-me` của `DB_PASSWORD`, `JWT_SECRET`, `WORKER_SERVICE_TOKEN` và `INITIAL_ADMIN_PASSWORD`. File `.env` đã được gitignore, không commit file này.

`NEXT_PUBLIC_API_BASE_URL` là URL browser dùng để gọi FastAPI. Khi chạy tất cả trên máy local, giữ `http://localhost:8000/api/v1`.

## Khởi động

```bash
docker compose up --build
```

Luồng khởi động:

```text
postgres (volume postgres_data)
  -> migrate (alembic upgrade head)
  -> backend
  -> frontend
```

Sau khi migration xong, tạo Admin đầu tiên một lần:

```bash
docker compose exec backend python -m app.seed
```

## Endpoints local

| Service | URL | Ghi chú |
|---|---|---|
| Frontend | http://localhost:3000 | UI Scenario Forge |
| Backend OpenAPI | http://localhost:8000/docs | FastAPI API contract |
| Backend health | http://localhost:8000/health/ready | Compose healthcheck |

PostgreSQL và Redis không publish port ra host; chỉ các container trong mạng Compose có thể truy cập. MinIO chưa được chạy trong giai đoạn này.

## Persistence

- `postgres_data`: users, roles, test cases, version, review, suite, run result, audit và pgvector.
- `redis_data`: cache/queue pointer ngắn hạn; không phải source of truth.

Không xóa named volume nếu cần giữ dữ liệu. Để dừng mà giữ dữ liệu, dùng `docker compose down`.
