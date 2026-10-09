# Scenario Forge — Local Docker stack

Compose giai đoạn hiện tại chạy Next.js frontend, FastAPI backend, agent2 sinh tình huống xe máy tạt đầu (`agent2/`) và PostgreSQL 17 + pgvector. CARLA/ScenarioRunner chạy ở máy Bridge.

`bridge/` là **Scenario Forge Bridge**, một CLI Python cài trên máy có CARLA (Ubuntu/Windows), không chạy trong Compose. Bridge ghép với Project bằng OTP 6 số ở trang Start up, giữ kết nối WebSocket với backend và báo trạng thái cổng CARLA 2000. Xem [bridge/README.md](bridge/README.md).

## Chuẩn bị cấu hình

```bash
cd /Users/minhquanvo/Documents/demo_fullstack_build-phase
cp .env.example .env
```

Trong `.env`, thay ít nhất các giá trị `change-me` của `DB_PASSWORD`, `JWT_SECRET`, `WORKER_SERVICE_TOKEN` và `INITIAL_ADMIN_PASSWORD`. Agent2 cần `OPENAI_API_KEY`, dùng `AGENT2_MODEL_NAME` (mặc định `gpt-4o-mini`) và `AGENT_API_KEY` dùng chung với Backend. File `.env` đã được gitignore, không commit file này.

`NEXT_PUBLIC_API_BASE_URL` là URL browser dùng để gọi FastAPI. Khi chạy tất cả trên máy local, giữ `http://localhost:8000/api/v1`.

## Khởi động

```bash
docker compose up --build
```

`docker compose up` tự nạp `docker-compose.override.yml` (chế độ dev): mount code `frontend/`, `backend/app`, `backend/migrations`, `agent2/app`, `agent2/knowledge` vào container; frontend chạy `next dev`, backend và agent2 chạy `uvicorn --reload`. Sửa code là tự reload, không cần build lại.

| Thay đổi | Lệnh |
|---|---|
| Code frontend/backend/agent2 | Không cần làm gì |
| Migration mới | `docker compose run --rm migrate` |
| `backend/requirements.txt` hoặc `agent2/requirements.txt` | `docker compose up -d --build backend migrate agent2` |
| `frontend/package.json` | `docker compose up -d --build -V frontend` |
| Chạy image production (bỏ override) | `docker compose -f docker-compose.yml up --build` |

Luồng khởi động:

```text
postgres (volume postgres_data)
  -> migrate (alembic upgrade head + python -m app.seed_catalog: dữ liệu CARLA mặc định)
  -> backend  --(AGENT_SERVICE_URL)-->  agent2 (nội bộ, cổng 8100)
  -> frontend
```

Sinh kịch bản bằng AI: mở **Kịch bản kiểm thử → Tạo kịch bản → Sinh bằng AI từ mô tả** (`/test-cases/generate`). Cần sync catalog từ Bridge để có `cut_in_sites`; xem [log bước 6](agent2/docs/06-buoc-6-xosc-va-ket-noi-agent2.md).

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

PostgreSQL publish port theo `POSTGRES_HOST_PORT`; các service còn lại giao tiếp qua mạng Compose.

## Persistence

- `postgres_data`: users, roles, test cases, version, review, suite, run result, audit và pgvector.

Không xóa named volume nếu cần giữ dữ liệu. Để dừng mà giữ dữ liệu, dùng `docker compose down`.
