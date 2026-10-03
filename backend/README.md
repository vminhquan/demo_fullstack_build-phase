# Scenario Forge — Flow B Backend

Backend này triển khai phần **quản lý vòng đời Test Case** theo các tài liệu trong `../docs`. Nó không chạy CARLA, ScenarioRunner hoặc agent sinh cảnh; các hệ thống đó là Worker Luồng A bên ngoài và chỉ giao tiếp qua contract execution.

## Kiến trúc

`app/` dùng layout module-first theo Clean Architecture:

```text
app/
├── shared/
│   ├── domain/                 # policy và lỗi nghiệp vụ, không phụ thuộc FastAPI/ORM
│   └── infrastructure/         # SQLAlchemy async, JWT/Argon2, audit
└── modules/
    ├── identity/               # user, project role + responsibilities, refresh sessions, RBAC
    ├── testcase/               # test case, version, tag, XOSC, search document
    ├── review/                 # submit, comment, approve/request-edit/reject
    ├── testsuite/              # suite, approved version pinning, batch snapshot
    ├── execution/              # run job/result + Worker Luồng A boundary
    ├── audit/                  # append-only audit query
    └── storage/                # StoragePort và MinIO adapter
```

## Điều bất biến được enforce

- Version chỉ sửa được khi `DRAFT`; `IN_REVIEW`, `EDIT`, `APPROVED`, `REJECTED` là immutable.
- Public register chỉ tạo `CREATOR`. `REVIEWER`/`ADMIN` phải được Admin gán qua API/seed.
- `ADMIN` không có `review:decide` nếu không đồng thời mang role `REVIEWER`.
- Submit review cần đủ 5 metadata và XOSC artifact.
- Request edit hoặc reject bắt buộc có lý do; mọi decision ghi audit cùng transaction.
- Test Suite lưu `test_case_version_id`, không lưu “latest Test Case”; chỉ version `APPROVED` mới được thêm. Database trigger là lớp phòng vệ thứ hai.
- Run snapshot tạo `run_jobs` khi bấm Run; suite đổi sau đó không làm run đang chạy thay đổi.
- `run_results.test_case_version_id` giữ đúng version đã chạy. Worker completion idempotent.
- File `.xosc`, log, video, ảnh nằm trong MinIO; PostgreSQL chỉ giữ `artifacts` metadata/checksum/object key bất biến.

## Database

Migration gốc `migrations/versions/0001_baseline.py` tạo PostgreSQL schema gồm:

- identity: `users`, `roles`, `responsibilities`, `project_users`, `project_user_responsibilities`, `auth_sessions`;
- catalog: `test_cases`, `test_case_versions`, `tags`, `test_case_version_tags`, `artifacts`;
- approval: `review_requests`, `review_comments`;
- execution: `test_suites`, `test_suite_items`, `test_suite_runs`, `run_jobs`, `run_results`, `run_result_artifacts`;
- observability/search: `audit_logs`, `test_case_search_documents` và pgvector HNSW index.

Migration gốc bật extension `vector`, seed bảng `roles` (ADMIN, MEMBER) và `responsibilities`, và thêm trigger `trg_suite_item_approved`. File này ghi rõ từng lệnh, không đọc model lúc chạy, nên chạy lại lúc nào cũng ra cùng một schema.

### Thay đổi schema

```bash
# 1. Sửa model trong app/shared/infrastructure/models.py
# 2. Sinh migration rồi ĐỌC LẠI file sinh ra trước khi commit
.venv/bin/alembic revision --autogenerate -m "add xyz"
# 3. Áp và kiểm tra model khớp DB (phải báo "No new upgrade operations detected")
.venv/bin/alembic upgrade head
.venv/bin/alembic check
```

Autogenerate không tự sinh extension, trigger, function hay dữ liệu seed; những thứ này phải viết tay bằng `op.execute` / `op.bulk_insert`. Autogenerate cũng không phát hiện việc thêm giá trị vào enum của PostgreSQL; dùng `op.execute("ALTER TYPE ... ADD VALUE ...")`.

### DB tạo bằng chuỗi migration cũ

Chuỗi cũ `0001`–`0007` được giữ trong `migrations/legacy/` chỉ để tham khảo, Alembic không đọc nữa. Khi `alembic upgrade head` gặp DB đang ở revision cũ, `migrations/legacy_bridge.py` (gọi từ `env.py`) xử lý trong cùng transaction:

| Revision hiện tại | Xử lý |
|---|---|
| `0007_roles_responsibilities` | Schema đã khớp baseline, chỉ đổi revision sang `0001_baseline` |
| `0006_suite_item_trigger` | Chạy phần chuyển đổi role/responsibility của 0007 cũ (SQL cố định), rồi đổi revision |
| `0001`–`0005` | Từ chối, phải nâng lên `0006` bằng bản phát hành còn chuỗi cũ trước |

Cầu nối chỉ chạy ở chế độ online, không chạy với `alembic upgrade head --sql`.

## Chạy local bằng Docker

Tại thư mục `demo_fullstack_build-phase`:

```bash
cp .env.example .env
# thay toàn bộ giá trị change-me bằng secret thật
docker compose up --build
docker compose run --rm migrate
```

API: `http://localhost:8000/docs`  
MinIO Console (development): `http://localhost:9001`

Compose chỉ chạy Backend + PostgreSQL/pgvector + Redis + MinIO. Không khởi động frontend hay Worker/CARLA vì chúng nằm ngoài scope của backend này.

## Chạy không dùng Docker

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# DATABASE_URL cần trỏ tới PostgreSQL có extension pgvector
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

Sau migration, tạo Admin đầu tiên bằng một lệnh explicit (không có Admin mặc định hay password hard-code):

```bash
INITIAL_ADMIN_EMAIL=admin@example.com \
INITIAL_ADMIN_PASSWORD='<mat-khau-manh>' \
.venv/bin/python -m app.seed
```

## Các API chính

- `POST /api/v1/auth/register|login|refresh|logout`, `GET /api/v1/auth/me`
- `/api/v1/projects/{id}/users...` — Admin quản lý role và responsibility của thành viên (xem `docs/06-auth-rbac-approval.md`)
- `POST/GET/PATCH /api/v1/test-cases`, tạo/sửa/clone version, upload/download XOSC
- `POST /api/v1/test-cases/search` — filter authoritative + keyword fallback nếu embedding tắt
- submit/comment/approve/request-edit/reject review
- CRUD Test Suite, add/reorder approved items, run batch snapshot, export request
- query Suite Run/Run Job/Run Result và cancel job
- integration endpoints/WS cho Worker Luồng A (không chứa CARLA logic)
- `GET /api/v1/audit-logs` — Admin only

## Worker Luồng A

Worker dùng `WORKER_SERVICE_TOKEN` qua `X-Worker-Token` cho REST integration hoặc query token cho `/api/v1/integration/worker/ws`. Backend chỉ quản lý:

```text
QUEUED -> CLAIMED -> RUNNING -> COMPLETED | FAILED | CANCELLED
```

Worker tự chịu trách nhiệm CARLA/ScenarioRunner. Khi cần output artifact lớn, bổ sung upload-intent/presigned URL vào `storage` module thay vì cho Worker ghi trực tiếp PostgreSQL/MinIO bằng credential người dùng.

## Kiểm tra

```bash
.venv/bin/ruff check app
.venv/bin/pytest
.venv/bin/alembic upgrade head --sql   # kiểm tra migration mà không kết nối DB
```
