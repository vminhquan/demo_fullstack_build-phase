# 10 — Testing Strategy và Acceptance Criteria

## 1. Test pyramid

```text
E2E: ít nhưng phủ demo nghiệm thu
Integration: PostgreSQL/MinIO/API contract
Unit: nhiều, tập trung domain/use cases
```

## 2. Backend unit tests bắt buộc

### Version rules

- DRAFT được edit.
- IN_REVIEW không được edit.
- APPROVED không được edit.
- REJECTED không được edit.
- clone tạo version_no tiếp theo.

### Review rules

- Creator không có `review:decide` -> forbidden.
- Reviewer approve IN_REVIEW -> approved.
- Reviewer reject mà thiếu comment -> validation error.
- approve version không ở IN_REVIEW -> conflict.
- hai decision đồng thời -> chỉ một commit thành công.

### Suite rules

- add approved version -> success.
- add draft/in-review/rejected -> conflict.
- suite item giữ nguyên version khi test case có version mới.

### Run rules

- result gắn đúng version.
- duplicate worker completion với cùng idempotency key không tạo result thứ hai.
- worker timeout/retry không làm mất job.

## 3. Integration tests

Dùng PostgreSQL thật bằng Testcontainers hoặc Docker Compose test profile.

Không dùng SQLite để test repository chính vì:

- enum;
- JSONB;
- locking;
- Postgres-specific constraints/indexes;
- concurrency semantics khác.

Test:

- migrations up/down where feasible;
- repository mapping;
- trigger approved suite item;
- transaction rollback;
- search filters combinations;
- audit row same transaction.

## 4. API contract tests

Tối thiểu:

```text
POST /auth/login
POST /test-cases
POST /test-cases/{id}/versions
POST /versions/{id}/submit-review
POST /reviews/{id}/approve
POST /reviews/{id}/reject
POST /test-suites/{id}/items
POST /test-suites/{id}/runs
GET  /suite-runs/{id}
GET  /audit-logs
```

OpenAPI JSON được CI snapshot/validate để Frontend không bị breaking change âm thầm.

## 5. Frontend tests

Component:

- status badge;
- permission-gated buttons;
- filter form -> URL params;
- review decision dialog;
- suite add item validation message.

E2E bằng Playwright:

### Scenario A — approve

```text
Creator login
-> create test case + v1
-> upload xosc
-> submit review
-> Reviewer login
-> approve + comment
-> add to suite
-> run suite
-> view result
-> Admin sees audit chain
```

### Scenario B — reject/rework

```text
Creator v1 submit
-> Reviewer reject + comment
-> v1 remains immutable REJECTED
-> Creator clone v2 DRAFT
-> edit + submit
-> Reviewer approve v2
-> suite can add v2, cannot add v1
```

## 6. Acceptance matrix theo Luồng B

| Requirement | Acceptance evidence |
|---|---|
| Auth + role | API + UI login; role/permission tests |
| Lưu test case/version | DB rows + version history UI |
| Lưu run result đúng version | `run_results.test_case_version_id` + UI link |
| Filter đủ trường | E2E/data-driven tests cho 6 filters |
| Reviewer-only approve | 403 cho non-reviewer, success cho reviewer |
| Approved-only suite | API 409 + DB trigger/invariant |
| Batch run | 202 -> jobs -> final results |
| Export | ZIP manifest pins versions/checksums |
| Audit | actor/action/entity/time trace đầy đủ |

## 7. Definition of Done cho mỗi feature

Một feature chỉ Done khi có:

- domain/application rule;
- API contract;
- migration nếu cần;
- authorization;
- audit action nếu là state-changing action;
- unit/integration test;
- UI happy/error/loading states;
- OpenAPI updated.


## Semantic/RAG search

Test tối thiểu:

- structured filter là authoritative: `status=APPROVED` không trả DRAFT/IN_REVIEW/EDIT/REJECTED;
- `map_code=Town05` không trả map khác dù vector similarity cao;
- semantic synonym: query `person crossing` có thể retrieve case `pedestrian crossing`;
- query `bike rider cuts into lane` retrieve cyclist merge cases;
- record chưa có embedding vẫn tìm được bằng structured/keyword fallback;
- semantic endpoint không bypass authorization scope;
- re-index sau khi title/description/metadata/tag thay đổi;
- embedding provider unavailable -> fallback hoặc error code rõ theo contract.

Dùng mock dataset trong `14-mock-test-case-data.md` để chạy acceptance matrix.


## MinIO acceptance tests

- Upload `.xosc` tạo object trong bucket và row `artifacts` có đúng `bucket_name/object_key/sha256/size_bytes`.
- Download yêu cầu authorization; user không đủ quyền không nhận presigned URL/object bytes.
- Replace XOSC ở DRAFT tạo object mới và cập nhật artifact reference, không silently overwrite Approved artifact.
- MinIO down -> API trả storage error rõ ràng và không tạo artifact metadata giả.
- Presigned URL hết hạn thì không dùng lại được; bucket vẫn private.
- Restart MinIO container không mất object vì dùng `minio_data` volume.
