# Scenario Forge — Local Docker stack

Compose giai đoạn hiện tại chạy Next.js frontend, FastAPI backend, Agent sinh kịch bản (`agent/`), PostgreSQL 17 + pgvector và Redis. CARLA/ScenarioRunner/Worker Luồng A và MinIO sẽ được thêm lại khi cần upload XOSC/log/video.

`bridge/` là **Scenario Forge Bridge**, một CLI Python cài trên máy có CARLA (Ubuntu/Windows), không chạy trong Compose. Bridge ghép với Project bằng OTP 6 số ở trang Start up, giữ kết nối WebSocket với backend và báo trạng thái cổng CARLA 2000. Xem [bridge/README.md](bridge/README.md).

## Chuẩn bị cấu hình

```bash
cd /Users/minhquanvo/Documents/demo_fullstack_build-phase
cp .env.example .env
```

Trong `.env`, thay ít nhất các giá trị `change-me` của `DB_PASSWORD`, `JWT_SECRET`, `WORKER_SERVICE_TOKEN` và `INITIAL_ADMIN_PASSWORD`. Agent dùng `OPENAI_API_KEY` + `MODEL_NAME` (để trống key thì sinh theo quy tắc offline) và `AGENT_API_KEY` (đặt giá trị bất kỳ, Backend và Agent dùng chung). File `.env` đã được gitignore, không commit file này.

`NEXT_PUBLIC_API_BASE_URL` là URL browser dùng để gọi FastAPI. Khi chạy tất cả trên máy local, giữ `http://localhost:8000/api/v1`.

## Khởi động

```bash
docker compose up --build
```

`docker compose up` tự nạp `docker-compose.override.yml` (chế độ dev): mount code `frontend/`, `backend/app`, `backend/migrations`, `agent/app`, `agent/knowledge` vào container; frontend chạy `next dev`, backend và agent chạy `uvicorn --reload`. Sửa code là tự reload, không cần build lại.

| Thay đổi | Lệnh |
|---|---|
| Code frontend/backend/agent | Không cần làm gì |
| Migration mới | `docker compose run --rm migrate` |
| `backend/requirements.txt` hoặc `agent/requirements.txt` | `docker compose up -d --build backend migrate agent` |
| `frontend/package.json` | `docker compose up -d --build -V frontend` |
| Chạy image production (bỏ override) | `docker compose -f docker-compose.yml up --build` |

Luồng khởi động:

```text
postgres (volume postgres_data)
  -> migrate (alembic upgrade head + python -m app.seed_catalog: dữ liệu CARLA mặc định)
  -> backend  --(AGENT_SERVICE_URL)-->  agent (nội bộ, cổng 8100)
  -> frontend
```

Sinh kịch bản bằng AI: mở **Kịch bản kiểm thử → Tạo kịch bản → Sinh bằng AI từ mô tả** (`/test-cases/generate`). Chi tiết: [docs/19-agent-sinh-kich-ban.md](docs/19-agent-sinh-kich-ban.md).

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
