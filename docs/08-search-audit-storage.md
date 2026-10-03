# 08 — Search, Audit và MinIO Object Storage

## 1. Search/filter MVP

Các filter bắt buộc:

```text
map_code
adversary_type
environment_code
danger_level
status
creator_id
```

Nên thêm:

```text
ego_vehicle_code
tag
q
created_from / created_to
sort
page / page_size
```

## 2. Nguyên tắc: filter chính xác trước, semantic search sau

Không dùng vector/RAG để thay thế điều kiện chính xác như `status=APPROVED` hay `map_code=Town05`.

```text
Structured filter -> PostgreSQL WHERE/index
Keyword search    -> PostgreSQL full-text search
Semantic search   -> pgvector cosine similarity
```

Khi user kết hợp cả query tự nhiên và filter, Backend tạo **hybrid query**.

Ví dụ:

```text
q = "người đi bộ băng qua đường trong mưa"
map_code = Town05
status = APPROVED
danger_level = HIGH
```

SQL filter giới hạn tập dữ liệu hợp lệ trước/đồng thời với vector ranking.

## 3. Projection cho list/search

Không load toàn bộ version history khi search.

```text
TestCaseSummary
  case_id
  case_key
  title
  creator
  latest_version_id
  latest_version_no
  latest_status
  map_code
  ego_vehicle_code
  adversary_type
  environment_code
  danger_level
  tags
  semantic_score?      # chỉ có ở semantic search
  updated_at
```

## 4. Application ports

```python
class TestCaseSearch(Protocol):
    async def search(self, criteria: SearchCriteria) -> Page[TestCaseSummary]: ...

class EmbeddingPort(Protocol):
    async def embed(self, text: str) -> list[float]: ...

class SemanticIndexPort(Protocol):
    async def upsert_version(self, version_id: UUID) -> None: ...
    async def search(self, query_vector, criteria, limit: int): ...
```

Implementations:

```text
PostgresTestCaseSearch
PgVectorSemanticIndex
ConfiguredEmbeddingProvider
```

Chi tiết semantic/RAG nằm trong `13-rag-search-filter.md`.

## 5. Audit actions

Chỉ ghi các hành động làm thay đổi dữ liệu. Thao tác chỉ đọc hoặc chỉ chuyển ngữ cảnh như đăng nhập (`USER_LOGIN_SUCCEEDED`) hay mở Project (`PROJECT_SELECTED`) không được ghi. Các dòng cũ của hai loại này vẫn còn trong DB nhưng bị ẩn khỏi API `/audit-logs`.

```text
USER_REGISTERED
USER_ROLE_CHANGED
TEST_CASE_CREATED
TEST_CASE_UPDATED
VERSION_CREATED
VERSION_XOSC_UPDATED
SEARCH_SEMANTIC_EXECUTED       # optional aggregate/diagnostic event
REVIEW_SUBMITTED
REVIEW_COMMENTED
REVIEW_APPROVED
REVIEW_REJECTED
SUITE_CREATED
SUITE_ITEM_ADDED
SUITE_ITEM_REMOVED
SUITE_RUN_REQUESTED
RUN_JOB_STARTED
RUN_JOB_COMPLETED
RUN_JOB_FAILED
SUITE_EXPORTED
```

Không nhất thiết audit từng keyword search của user nếu không có yêu cầu compliance. Nếu log search analytics, tránh lưu dữ liệu nhạy cảm không cần thiết.

## 6. Audit payload

Ví dụ approve:

```json
{
  "actor_user_id": "reviewer-id",
  "action": "REVIEW_APPROVED",
  "entity_type": "TEST_CASE_VERSION",
  "entity_id": "version-id",
  "request_id": "request-id",
  "before_data": {"status": "IN_REVIEW"},
  "after_data": {"status": "APPROVED"}
}
```

Không ghi vào audit:

- raw password;
- JWT/refresh token;
- secret/API key;
- full binary XOSC/video;
- sensitive headers.

## 7. Atomic audit

Business state + audit row nên nằm trong cùng PostgreSQL transaction:

```text
UPDATE version status
UPDATE review decision
INSERT review comment
INSERT audit_log
COMMIT
```

Với event đưa ra Redis/semantic indexing, có thể dùng outbox nếu cần reliability cao:

```text
outbox_events(id, event_type, payload, published_at)
```

## 8. MinIO Object Storage

Luồng B dùng **MinIO** làm object storage cho binary/file artifact. PostgreSQL không lưu file binary.

Bucket đề xuất cho MVP:

```text
scenario-forge-artifacts
```

Object key:

```text
xosc/{case_id}/{version_id}/{artifact_id}.xosc
runs/{run_job_id}/logs/{artifact_id}.log
runs/{run_job_id}/video/{artifact_id}.mp4
runs/{run_job_id}/screenshots/{artifact_id}.png
exports/{suite_id}/{export_id}.zip
```

Không dùng tên file user cung cấp làm object key trực tiếp. `original_name` chỉ là metadata để hiển thị/download.

## 9. StoragePort trong Clean Architecture

Application phụ thuộc interface, không phụ thuộc MinIO SDK:

```python
class StoragePort(Protocol):
    async def put(self, *, object_key: str, stream, size: int, content_type: str) -> StoredObject: ...
    async def open(self, *, object_key: str): ...
    async def stat(self, *, object_key: str) -> StoredObject: ...
    async def delete(self, *, object_key: str) -> None: ...
    async def presigned_get(self, *, object_key: str, expires_seconds: int) -> str: ...
    async def presigned_put(self, *, object_key: str, expires_seconds: int) -> str: ...
```

Infrastructure implement:

```text
MinioStorageAdapter -> MinIO Python SDK / S3-compatible API
```

Domain/Application không import `minio` package. Unit test dùng `FakeStorage`.

## 10. Artifact metadata và integrity

Database lưu:

```text
storage_provider = MINIO
bucket_name
object_key
etag
sha256
size_bytes
content_type
original_name
created_at
```

`sha256` là checksum chuẩn của ứng dụng. `etag` chỉ lưu để debug/conditional request nếu cần; không giả định ETag luôn bằng MD5/checksum nội dung.

Upload flow qua Backend:

1. authorize user/action;
2. validate extension/content type/size;
3. tính SHA-256 khi stream;
4. tạo immutable object key;
5. `put_object` vào MinIO;
6. transaction PostgreSQL insert artifact + liên kết entity + audit;
7. nếu DB fail sau upload thì cleanup object bằng compensating action.

## 11. Presigned URL

MVP có thể stream mọi file qua FastAPI. Khi log/video lớn, dùng presigned URL để Backend không phải relay toàn bộ bytes:

```text
Browser/Worker
   -> FastAPI: request upload/download
   -> FastAPI: RBAC + ownership + create object key
   <- short-lived presigned URL
   -> MinIO: PUT/GET trực tiếp
```

Presigned URL phải:

- TTL ngắn, ví dụ 5–15 phút;
- chỉ cho đúng object key;
- được cấp sau authorization;
- bucket vẫn private;
- không log full signed URL vì URL chứa thông tin xác thực tạm thời.

## 12. File lifecycle

Không xóa object ngay khi test case bị archive nếu artifact còn được version/run/suite tham chiếu.

MVP:

- immutable object key;
- replace XOSC = upload object mới, cập nhật `xosc_artifact_id`;
- soft-delete metadata trước;
- background cleanup object orphan sau grace period;
- audit không bị cascade delete.

Có thể bật bucket versioning về sau, nhưng **domain versioning của TestCase vẫn là source of truth**; không dùng MinIO object version để thay thế `test_case_versions`.

## 13. Security

- Không public bucket.
- Frontend không nhận `MINIO_ROOT_USER/MINIO_ROOT_PASSWORD`.
- Backend dùng service credential riêng; production nên tạo user/policy quyền tối thiểu thay vì dùng root credential.
- Chặn path/object-key do user tự chỉ định.
- Validate content type/size và giới hạn upload.
- Secret nằm trong environment/secret manager, không commit `.env`.

## 14. Backup và failure

MinIO volume giữ object qua container restart nhưng **volume không phải backup**. Cần backup cả:

```text
PostgreSQL dump/base backup
+
MinIO bucket/object data
```

Nếu MinIO unavailable:

- metadata/search/review không nhất thiết phải down toàn bộ;
- upload/download trả storage error rõ ràng;
- không tạo artifact metadata giả;
- run-result finalize chỉ complete khi artifact policy bắt buộc đã persist.

Chi tiết container/config ở `09-docker-deployment.md`; thiết kế bucket/presigned URL ở `15-minio-object-storage.md`.
