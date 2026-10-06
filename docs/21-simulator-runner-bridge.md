# 21 — Simulator Runner qua Bridge: giao thức gửi test case và nhận kết quả

> **Trạng thái: đã triển khai ở backend** (migration `0008_bridge_runs`, `backend/app/modules/bridge/runs.py`, `runs_router.py`, `hub.py`). Phía Bridge (`bridge/scenario_forge_bridge`) **chưa** xử lý các message dưới đây — đây là hợp đồng để viết phần đó. Thay cho mục `job.*` trong [doc 18 §4.2](18-worker-carla-vps.md).

## 1. Luồng

```
FE Test Suite → chọn test case → Simulator Runner → chọn Bridge
  POST /api/v1/projects/{project_id}/simulator-runs {connection_uid, test_case_ids}
BE: khóa test case Đã phê duyệt có .xosc → tạo test_suite_runs + run_jobs (QUEUED)
BE ──run.assign (cả phiên, XOSC kèm theo)──────────────▶ Bridge
BE ◀──run.accepted──────────────────────────────────── Bridge   (jobs → CLAIMED)
       với mỗi test case:  job.started → job.completed | job.failed   (BE trả ack từng message)
BE ◀──run.completed──────────────────────────────────── Bridge
```

**Đã chạy thì không hủy:** không có thao tác hủy phiên chạy, BE không gửi lệnh dừng xuống Bridge. Phiên kết thúc khi Bridge báo kết quả mọi test case (hoặc BE tự đánh `TIMEOUT` / `MISSING_RESULT`).

- Kênh: WebSocket có sẵn `wss://<host>/api/v1/bridge/ws`, header `Authorization: Bearer <device_token>`.
- Mọi id là **BIGINT**. Một test case chỉ xuất hiện một lần trong một phiên → khóa của một job là **`run_id` + `test_case_id`**.
- **Bridge phải mở WebSocket với `max_size` đủ lớn** (vd. `websockets.connect(..., max_size=None)` hoặc ≥ 16 MB): `run.assign` chứa nội dung XOSC của mọi test case; mặc định của thư viện `websockets` là 1 MiB.

## 2. BE → Bridge

### `run.assign` — gửi cả phiên chạy

```json
{
  "type": "run.assign",
  "run_id": 77,
  "project_id": 12,
  "test_cases": [
    {
      "test_case_id": 171,
      "case_key": "TC-000171",
      "revision": 2,
      "map_name": "Town04",
      "timeout_s": 300,
      "xosc_sha256": "9f3c…(64 hex)",
      "xosc": "<?xml version=\"1.0\"?><OpenSCENARIO>…</OpenSCENARIO>"
    }
  ]
}
```

| Trường | Ý nghĩa / Bridge cần làm gì |
|---|---|
| `run_id` | Id phiên chạy. **Nhận trùng `run_id` là bình thường** (gửi lại) → không chạy lại, chỉ trả `run.accepted` lần nữa |
| `project_id` | Project của phiên (một Bridge có thể ghép nhiều project) |
| `test_cases` | Chỉ các test case **chưa có kết quả**, đã **sắp theo `map_name`** → chạy đúng thứ tự để ít lần đổi map |
| `map_name` | Map CARLA (lấy từ dữ liệu đã đồng bộ). Khác map đang mở → `client.load_world(map_name)` |
| `xosc` | Nội dung OpenSCENARIO (UTF-8). Tình huống (xe ego, tác nhân, thời tiết, điểm xuất phát) nằm trong file này |
| `xosc_sha256` | `sha256(xosc.encode("utf-8"))` phải bằng giá trị này, lệch → `job.failed XOSC_HASH_MISMATCH` |
| `revision` | Gửi lại nguyên giá trị trong `job.completed` |
| `timeout_s` | Giới hạn mỗi test case. Quá `timeout_s + 60` giây ở trạng thái RUNNING mà không có kết quả, BE tự đánh `FAILED TIMEOUT` |

**Khi nào BE gửi:** ngay khi tạo phiên; khi Bridge kết nối (lại) — mọi phiên còn test case chưa xong; và mỗi ~20 giây nếu chưa nhận `run.accepted`.

## 3. Bridge → BE

Mọi message dưới đây được BE trả lời bằng `ack` hoặc `error`. **Bridge giữ message (lưu file) cho tới khi nhận `ack`**, mất kết nối thì gửi lại — BE xử lý idempotent.

### `run.accepted` — đã nhận phiên

```json
{ "type": "run.accepted", "run_id": 77 }
```

### `run.rejected` — không thể chạy cả phiên (BE đánh mọi job còn lại `FAILED` với `error_code = reason`)

```json
{ "type": "run.rejected", "run_id": 77, "reason": "CARLA_UNREACHABLE", "message": "Không kết nối được 127.0.0.1:2000" }
```

### `job.started`

```json
{ "type": "job.started", "run_id": 77, "test_case_id": 171 }
```

### `job.completed` — ScenarioRunner chạy xong

```json
{
  "type": "job.completed",
  "run_id": 77,
  "test_case_id": 171,
  "revision": 2,
  "xosc_sha256": "9f3c…",
  "map_name": "Town04",
  "verdict": "FAIL",
  "exit_code": 1,
  "duration_ms": 41230,
  "metrics": {
    "collision": true,
    "collision_count": 1,
    "min_ttc_seconds": 0.42,
    "criteria": [{ "name": "CollisionTest", "result": "FAILURE", "actual": 1, "expected": 0 }]
  }
}
```

| Trường | Bắt buộc | Ghi chú |
|---|---|---|
| `revision`, `xosc_sha256` | ✔ | Lấy đúng từ `run.assign`. Lệch với test case đã khóa → BE ghi `FAILED RESULT_MISMATCH` |
| `verdict` | ✔ | `PASS` \| `FAIL` \| `ERROR` |
| `exit_code`, `duration_ms`, `map_name` | | |
| `metrics` | | Lưu nguyên vào `run_results.metrics`. Giao diện đọc `collision`, `collision_count`, `min_ttc_seconds` |

### `job.failed` — không chạy được test case

```json
{ "type": "job.failed", "run_id": 77, "test_case_id": 172, "error_code": "MAP_LOAD_FAILED", "error_message": "Town04 chưa cài" }
```

`error_code` gợi ý (giao diện có nhãn tiếng Việt): `CARLA_UNREACHABLE`, `MAP_LOAD_FAILED`, `XOSC_HASH_MISMATCH`, `SCENARIO_RUNNER_ERROR`, `TIMEOUT`, `WORKER_RESTARTED`. Mã khác vẫn được lưu và hiện nguyên văn.

### `run.completed` — đã chạy hết danh sách

```json
{ "type": "run.completed", "run_id": 77 }
```

Test case nào chưa được báo kết quả sẽ bị đánh `FAILED MISSING_RESULT`.

### Phản hồi của BE

```json
{ "type": "ack", "ref": "job.completed", "run_id": 77, "test_case_id": 171 }
{ "type": "ack", "ref": "job.completed", "run_id": 77, "test_case_id": 171, "duplicate": true }
{ "type": "error", "ref": "job.completed", "code": "RUN_NOT_FOUND", "message": "…", "run_id": 999 }
```

| `code` lỗi | Khi nào |
|---|---|
| `INVALID_MESSAGE` | Thiếu / sai kiểu trường |
| `RUN_NOT_FOUND` | Phiên không tồn tại, không giao cho Bridge này, hoặc Bridge đã bị gỡ khỏi project |
| `TEST_CASE_NOT_IN_RUN` | `test_case_id` không thuộc phiên |

## 4. Trạng thái

| `run_jobs.status` | Do message |
|---|---|
| `QUEUED` | Vừa tạo / đã gửi `run.assign` nhưng chưa được nhận |
| `CLAIMED` | `run.accepted` |
| `RUNNING` | `job.started` |
| `COMPLETED` (+ `run_results`) | `job.completed` khớp revision/sha |
| `FAILED` | `job.failed`, `run.rejected`, `RESULT_MISMATCH`, `TIMEOUT`, `MISSING_RESULT` |

Trạng thái phiên (`test_suite_runs.status`): `QUEUED` → `RUNNING` → `COMPLETED` (không job nào FAILED) / `FAILED` (có job FAILED).

## 5. Nhiều tiến trình backend

Socket của một Bridge nằm ở một tiến trình. Các tiến trình trao đổi qua Postgres `LISTEN/NOTIFY` kênh `scenario_forge_hub`:
- Tạo phiên ở tiến trình A, Bridge nối vào B → A gửi `{kind: dispatch, bridge_id, run_id}`, B tự đọc DB và gửi `run.assign` (XOSC không đi qua NOTIFY vì giới hạn 8000 byte).
- `catalog.sync.request` và sự kiện cho trang web cũng được chuyển tiếp như vậy.
- Bridge online = có tiến trình giữ socket (`bridges.online_since`) và heartbeat trong 45 giây gần nhất.

## 6. API cho giao diện

| Method | Path (dưới `/api/v1/projects/{project_id}`) | Quyền |
|---|---|---|
| `POST` | `/simulator-runs` `{connection_uid, test_case_ids}` → 202, có `skipped` (`NOT_APPROVED`, `NOT_FOUND`, `NO_XOSC`) | `suite:run` |
| `GET` | `/simulator-runs` | `suite:read` |
| `GET` | `/simulator-runs/{run_id}` (kèm từng test case) | `suite:read` |
| `GET` | `/simulator-runs/by-case/{test_case_id}` | `suite:read` |
