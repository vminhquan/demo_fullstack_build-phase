# 17 — Triển khai lên Render

Repo có sẵn file [`render.yaml`](../render.yaml) (Render Blueprint). Một lần tạo Blueprint sẽ dựng ba thành phần:

| Thành phần | Tên trên Render | Cách chạy |
|---|---|---|
| PostgreSQL (có pgvector) | `scenario-forge-db` | Postgres do Render quản lý |
| Backend FastAPI | `scenario-forge-api` | Docker, từ `backend/Dockerfile` |
| Frontend Next.js | `scenario-forge-web` | Node, build bằng `npm run build` |

Worker CARLA và ScenarioRunner **không chạy trên Render** (cần máy có GPU). Các màn xem kịch bản, duyệt, bộ kiểm thử, phân quyền vẫn dùng bình thường; lượt chạy chỉ hoàn tất khi có worker thật kết nối tới backend.

## 1. Chuẩn bị

1. Code (gồm `render.yaml`) đã nằm trên nhánh `main` của GitHub.
2. Có tài khoản Render và đã cho Render quyền đọc repo GitHub này.

## 2. Tạo Blueprint

1. Render Dashboard → **New** → **Blueprint**.
2. Chọn repo `demo_fullstack_build-phase`, nhánh `main`. Render đọc `render.yaml` và liệt kê 1 database + 2 web service.
3. Render hỏi giá trị cho hai biến đánh dấu `sync: false`. Điền theo tên service mặc định:

   | Service | Biến | Giá trị |
   |---|---|---|
   | `scenario-forge-api` | `CORS_ORIGINS` | `https://scenario-forge-web.onrender.com` |
   | `scenario-forge-web` | `NEXT_PUBLIC_API_BASE_URL` | `https://scenario-forge-api.onrender.com/api/v1` |

4. Bấm **Apply**. Lần đầu mất vài phút: tạo database → build backend → backend tự chạy migration (`RUN_MIGRATIONS=1`) → build frontend.

Các biến còn lại được đặt sẵn: `DATABASE_URL` lấy từ database, `JWT_SECRET` và `WORKER_SERVICE_TOKEN` do Render tự sinh ngẫu nhiên, `APP_ENV=production`, `EMBEDDING_PROVIDER=disabled`.

### Nếu URL thật khác URL ở trên

Tên `*.onrender.com` đã có người dùng thì Render thêm hậu tố, ví dụ `scenario-forge-api-x1y2.onrender.com`. Xem URL thật ở trang từng service, rồi:

1. Sửa `CORS_ORIGINS` của backend thành URL frontend thật → **Save** (backend tự khởi động lại).
2. Sửa `NEXT_PUBLIC_API_BASE_URL` của frontend thành `<URL backend thật>/api/v1` → **Manual Deploy → Clear build cache & deploy**. Biến này được gắn vào code lúc build, nên chỉ lưu biến mà không build lại thì không có tác dụng.

`CORS_ORIGINS` nhận nhiều domain, ngăn cách bằng dấu phẩy (ví dụ thêm domain riêng của bạn).

## 3. Kiểm tra sau khi deploy

| Kiểm tra | Kết quả mong đợi |
|---|---|
| `https://<backend>/health/ready` | `{"status":"ready"}` |
| `https://<backend>/docs` | Trang Swagger của API |
| Log backend lúc khởi động | `Running upgrade -> 0001_baseline` ở lần đầu, sau đó `Uvicorn running on http://0.0.0.0:<PORT>` |
| `https://<frontend>/login` | Trang đăng nhập; đăng nhập không báo lỗi kết nối Backend |

## 4. Tài khoản đầu tiên

Không cần tạo admin trước: mở `https://<frontend>/register`, đăng ký, rồi **Tạo Project**. Người tạo Project tự động là Quản trị viên với đủ nhiệm vụ; sau đó mời người khác bằng email ở màn **Phân quyền**.

Nếu muốn dùng script seed như khi chạy local (`python -m app.seed`), cần mở **Shell** của service backend và đặt `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_PASSWORD`. Shell chỉ có ở gói trả phí của Render.

## 5. Những thay đổi trong code để chạy được trên Render

| File | Thay đổi | Ảnh hưởng tới Docker Compose ở máy |
|---|---|---|
| `backend/app/shared/config.py` | Tự đổi `postgres://` / `postgresql://` thành `postgresql+asyncpg://` | Không (compose đã dùng `+asyncpg`) |
| `backend/start.sh`, `backend/Dockerfile` | Nghe cổng `$PORT` (mặc định 8000); chạy `alembic upgrade head` trước khi khởi động nếu `RUN_MIGRATIONS=1` | Không (compose không đặt hai biến này, migration vẫn chạy ở service `migrate`) |
| `render.yaml` | Blueprint ở trên | Không |

Đã kiểm tra ở máy: image backend chạy với `PORT=10000`, `RUN_MIGRATIONS=1`, `APP_ENV=production` và URL dạng `postgresql://` → migration chạy, `/health/ready` trả `ready`, đăng ký/đăng nhập được, CORS trả đúng domain; khởi động lại không chạy migration lần hai và dữ liệu còn nguyên. Lệnh build/start của frontend trong `render.yaml` cũng chạy được và gắn đúng URL API vào bundle.

## 6. Giới hạn và lưu ý

> Các điểm dưới đây về gói Render là theo hiểu biết hiện có, **chưa kiểm chứng lại** với tài liệu Render mới nhất; chính sách gói miễn phí có thể thay đổi.

- **Gói free tự ngủ** khi không có truy cập một thời gian; lần mở đầu tiên sau đó chậm vài chục giây. Frontend có thể báo lỗi kết nối trong lúc backend đang khởi động lại — tải lại trang sau ít giây.
- **Database free có thời hạn sử dụng**; hết hạn sẽ mất dữ liệu nếu không nâng cấp. Dữ liệu thật nên dùng gói trả phí.
- **pgvector**: migration chạy `CREATE EXTENSION IF NOT EXISTS vector`. Nếu database không cho phép extension này, migration sẽ báo lỗi ngay ở lần deploy đầu — xem log backend.
- **Tệp XOSC** được lưu trong PostgreSQL (bảng `artifacts`), nên không cần ổ đĩa riêng cho backend.
- **Không commit file `.env`**; mọi bí mật trên Render được đặt bằng biến môi trường của service.

## 7. Xử lý sự cố

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| Frontend báo "Không thể kết nối tới Backend" | `NEXT_PUBLIC_API_BASE_URL` sai, hoặc đổi biến mà chưa build lại | Sửa biến → Clear build cache & deploy frontend |
| Trình duyệt báo lỗi CORS | `CORS_ORIGINS` không khớp chính xác URL frontend (có `https://`, không có `/` ở cuối) | Sửa `CORS_ORIGINS` của backend |
| Backend thoát ngay với lỗi `JWT_SECRET and WORKER_SERVICE_TOKEN must be set outside development` | Thiếu hai biến bí mật khi `APP_ENV=production` | Đặt lại hai biến (hoặc tạo lại từ Blueprint để Render sinh) |
| Backend lỗi lúc migration | Database chưa sẵn sàng hoặc không bật được extension `vector` | Xem log; deploy lại sau khi database ở trạng thái Available |
| Health check thất bại | App không nghe đúng `$PORT` | Đảm bảo service dùng `backend/Dockerfile` hiện tại (chạy `start.sh`) |
