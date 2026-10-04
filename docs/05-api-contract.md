# 05 — REST API Contract

Base path:

```text
/api/v1
```

Response lỗi thống nhất:

```json
{
  "error": {
    "code": "VERSION_NOT_APPROVED",
    "message": "Only approved test case versions can be added to a suite",
    "details": {},
    "request_id": "8d8b..."
  }
}
```

## 1. Auth

```http
POST /auth/register
POST /auth/login
POST /auth/refresh
POST /auth/logout
GET  /auth/me
```

Register request:

```json
{
  "email": "creator@example.com",
  "password": "...",
  "display_name": "Creator A"
}
```

Public registration luôn tạo role `CREATOR`. Không cho client gửi `role=REVIEWER`.

## 2. User/admin

```http
GET   /users                     # Admin
GET   /users/{id}                # Admin / self as allowed
PATCH /users/{id}/roles          # Admin
PATCH /users/{id}/active         # Admin
```

Assign role:

```json
{
  "roles": ["CREATOR", "REVIEWER"]
}
```

## 3. Test cases

```http
POST /test-cases
GET  /test-cases
GET  /test-cases/{case_id}
PATCH /test-cases/{case_id}      # stable container fields only
```

Create request:

```json
{
  "title": "Pedestrian crossing in rain",
  "description": "..."
}
```

Search:

```http
GET /test-cases?map_code=Town05
    &adversary_type=pedestrian
    &environment_code=heavy_rain
    &danger_level=HIGH
    &status=APPROVED
    &creator_id=<uuid>
    &tag=night
    &q=crossing
    &sort=-updated_at
    &page=1
    &page_size=20
```

### Semantic / hybrid search

```http
POST /test-cases/search
```

```json
{
  "query": "pedestrian suddenly crosses road in heavy rain",
  "mode": "hybrid",
  "filters": {
    "map_code": ["Town05"],
    "status": ["APPROVED"],
    "danger_level": ["HIGH", "CRITICAL"]
  },
  "page": 1,
  "page_size": 20
}
```

Structured filter là authoritative; semantic score chỉ dùng retrieval/ranking. Xem `13-rag-search-filter.md`.

Response item nên trả latest version snapshot:

```json
{
  "id": "case-uuid",
  "case_key": "TC-000123",
  "title": "Pedestrian crossing in rain",
  "created_by": {"id": "...", "display_name": "Creator A"},
  "latest_version": {
    "id": "version-uuid",
    "version_no": 3,
    "status": "APPROVED",
    "map_code": "Town05",
    "adversary_type": "pedestrian",
    "environment_code": "heavy_rain",
    "danger_level": "HIGH"
  }
}
```

## 4. Version API

```http
POST /test-cases/{case_id}/versions
GET  /test-cases/{case_id}/versions
GET  /test-case-versions/{version_id}
PATCH /test-case-versions/{version_id}       # DRAFT only
POST /test-case-versions/{version_id}/xosc  # upload/replace DRAFT only; Backend -> MinIO
GET  /test-case-versions/{version_id}/xosc  # authorize + stream/presigned download
POST /test-case-versions/{version_id}/clone # create next DRAFT
```

Create version request:

```json
{
  "map_code": "Town05",
  "ego_vehicle_code": "vehicle.tesla.model3",
  "adversary_type": "pedestrian",
  "environment_code": "heavy_rain",
  "danger_level": "HIGH",
  "scenario_input": {},
  "tag_names": ["night", "crossing"],
  "change_note": "Increase adversary speed"
}
```

XOSC MVP có thể upload multipart qua FastAPI; Backend stream object sang MinIO:

```http
POST /test-case-versions/{id}/xosc
Content-Type: multipart/form-data
file=<scenario.xosc>
```

Backend trả:

```json
{
  "artifact_id": "...",
  "sha256": "...",
  "size_bytes": 38219
}
```

## 5. Review API

```http
POST /test-case-versions/{version_id}/submit-review
GET  /reviews
GET  /reviews/{review_id}
POST /reviews/{review_id}/comments
POST /reviews/{review_id}/approve
POST /reviews/{review_id}/request-edit
POST /reviews/{review_id}/reject
```

Submit:

```json
{
  "message": "Please review version 3"
}
```

Decision:

```json
{
  "comment": "Validated against acceptance criteria"
}
```

`request-edit` và `reject` đều yêu cầu comment ở backend. `request-edit` đưa version về outcome `EDIT`; Creator clone version đó thành `DRAFT` mới để chỉnh metadata/XOSC và gửi lại review.

## 6. Test Suite API

```http
POST   /test-suites
GET    /test-suites
GET    /test-suites/{suite_id}
PATCH  /test-suites/{suite_id}
DELETE /test-suites/{suite_id}

POST   /test-suites/{suite_id}/items
DELETE /test-suites/{suite_id}/items/{version_id}
PATCH  /test-suites/{suite_id}/items/reorder

POST   /test-suites/{suite_id}/runs
GET    /test-suites/{suite_id}/runs
POST   /test-suites/{suite_id}/export
```

Add item:

```json
{
  "test_case_version_id": "..."
}
```

Backend kiểm tra version hiện là `APPROVED` trước insert.

Run suite returns async job:

```http
HTTP/1.1 202 Accepted
```

```json
{
  "suite_run_id": "...",
  "status": "QUEUED",
  "total_jobs": 12
}
```

## 7. Run API

```http
GET  /suite-runs/{suite_run_id}
GET  /suite-runs/{suite_run_id}/jobs
GET  /run-jobs/{job_id}
GET  /run-results/{result_id}
GET  /artifacts/{artifact_id}/download
POST /artifacts/{artifact_id}/presigned-download  # optional, short TTL
POST /run-jobs/{job_id}/cancel
```

Run detail:

```json
{
  "job_id": "...",
  "test_case_version": {"id": "...", "version_no": 3},
  "status": "COMPLETED",
  "result": {
    "verdict": "FAIL",
    "metrics": {
      "collision_count": 1,
      "lane_invasion_count": 0,
      "duration_sec": 42.3
    },
    "artifacts": [
      {"id": "...", "role": "LOG"},
      {"id": "...", "role": "VIDEO"}
    ]
  }
}
```

## 8. Artifact / MinIO access API

Client không nhận MinIO access key/secret key. Có hai mode:

```text
Mode A — MVP/simple
Browser/Worker -> FastAPI -> MinIO

Mode B — file lớn
Client -> FastAPI authorize -> presigned URL -> MinIO
```

Endpoint tùy chọn cho direct upload file lớn:

```http
POST /artifacts/upload-intents
POST /artifacts/upload-intents/{intent_id}/finalize
```

Create upload intent request:

```json
{
  "kind": "RUN_VIDEO",
  "original_name": "run.mp4",
  "content_type": "video/mp4",
  "size_bytes": 104857600,
  "sha256": "client-computed-or-null-by-policy"
}
```

Backend tạo immutable `object_key` và presigned PUT URL với TTL ngắn. Sau upload, `finalize` phải `stat_object`/verify expected metadata trước khi gắn artifact vào domain entity. Bucket không public.

## 9. Audit API

```http
GET /audit-logs?action=REVIEW_APPROVED
                &actor_user_id=<uuid>
                &entity_type=TEST_CASE_VERSION
                &entity_id=<uuid>
                &from=...
                &to=...
                &page=1
```

Admin only.

## 9b. CARLA catalog & Agent sinh kịch bản

```http
GET  /carla-catalog/snapshots?source=DEFAULT|PROJECT
GET  /carla-catalog/snapshots/{snapshot_id}
POST /carla-catalog/snapshots/import          # Admin (catalog:manage), body {catalog: catalog.v1, label?}
POST /scenario-generations                    # testcase:create, body {prompt, catalog_source, catalog_snapshot_id?, seed?}
GET  /scenario-generations/{generation_id}
POST /scenario-generations/{generation_id}/accept   # tạo Test Case/version DRAFT + XOSC artifact
```

Contract, mã lỗi và cách map 5 metadata: [19-agent-sinh-kich-ban.md](19-agent-sinh-kich-ban.md).

## 10. Integration contract với Worker Luồng A

Worker không thuộc implementation scope của Luồng B. Theo boundary đã chốt ở `01-system-architecture.md`, Worker Luồng A kết nối outbound bằng WebSocket/WSS.

Luồng B cần thống nhất message/payload tối thiểu:

```text
worker.hello
worker.heartbeat
job.assigned
job.started
job.progress
job.completed
job.failed
```

Luồng B persist `run_job`, `run_result`, artifact metadata và audit. Chi tiết WebSocket lifecycle/CARLA/ScenarioRunner do Luồng A verify và triển khai.

## 11. Idempotency

Nên hỗ trợ header:

```http
Idempotency-Key: <uuid>
```

cho:

- create run;
- worker complete/fail;
- export suite;
- các command dễ bị retry qua network.
