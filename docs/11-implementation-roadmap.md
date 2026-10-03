# 11 — Implementation Roadmap

Mục tiêu là có vertical slice chạy được sớm, không xây hết layer rồi mới nối.

## Phase 0 — Chốt contract Luồng B

Phải chốt trước:

```text
5 required metadata fields
DangerLevel taxonomy
TestCaseVersion DTO
RunResult DTO
Artifact types
Review status/state transition
```

Đây là nền tảng để Backend, Frontend và Database triển khai thống nhất.

## Phase 1 — Foundation

Backend:

- FastAPI skeleton + module layout.
- SQLAlchemy async + Alembic.
- PostgreSQL migrations.
- UnitOfWork.
- error response standard.
- request_id middleware.

Frontend:

- Next.js app shell.
- HTTP client.
- auth/session context.
- protected layout.

Infra:

- compose frontend/backend/postgres-pgvector/redis/minio; tạo bucket `scenario-forge-artifacts`.
- healthchecks.

Deliverable: `docker compose up` mở được frontend và backend health.

## Phase 2 — Identity + RBAC

- register Creator;
- login/refresh/logout;
- users/roles;
- permission map;
- Admin role assignment;
- `/auth/me`.

Acceptance: Creator gọi approve -> 403; Reviewer gọi approve endpoint về tới business layer.

## Phase 3 — Test Case + Version + Storage

- test case CRUD;
- create draft version;
- upload XOSC vào MinIO qua `StoragePort -> MinioStorageAdapter`;
- tags;
- version history;
- checksum;
- draft edit/clone.

Frontend: list/detail/create/version history.

Acceptance: reload/restart services không mất file/database data.

## Phase 4 — Search/filter + RAG retrieval

- 6 filter chính;
- pagination/sort;
- B-tree/GIN indexes;
- PostgreSQL full-text search;
- pgvector search document + embedding;
- hybrid search API;
- URL-driven filters UI;
- mock dataset trong `14-mock-test-case-data.md`.

Acceptance: automated filter matrix + semantic-query relevance set; explicit filter không bị semantic ranking bypass.

## Phase 5 — Review workflow

- submit;
- review queue;
- comments;
- approve/request-edit/reject;
- immutable reviewed versions;
- audit.

Frontend: Reviewer screen + XOSC/metadata diff.

Acceptance: demo Creator -> Reviewer request-edit hoặc approve/reject.

## Phase 6 — Test Suite

- suite CRUD;
- approved-only add;
- pinned version;
- reorder/remove;
- export package.

Acceptance: API + DB không thể add non-approved version.

## Phase 7 — Execution integration

- suite run snapshot;
- run jobs;
- Redis queue;
- integration contract với Worker Luồng A qua WebSocket/WSS;
- result/artifact upload;
- retry/error handling;
- progress UI.

Acceptance: batch 3–5 test case `APPROVED` chạy từ UI tới result.

## Phase 8 — Audit + hardening

- admin audit viewer;
- rate limits login;
- token revocation;
- idempotency;
- backup/restore test;
- MinIO object lifecycle/orphan cleanup policy;
- observability/log correlation.

## Suggested work split

### Backend lead

- shared schema/API contract;
- DB/migrations;
- auth/RBAC;
- test case/review/suite APIs;
- queue integration.

### Integration owner với Luồng A

- thống nhất WebSocket message contract;
- map job/result payload giữa Luồng B và Worker Luồng A;
- artifact/metric handoff;
- heartbeat/retry semantics được verify ở Luồng A.

### Frontend engineer

- auth;
- list/filter;
- detail/version;
- review;
- suite/run result.

## PR strategy

Không làm PR “build whole backend”. Chia vertical slices:

```text
feat/auth-login
feat/testcase-create-v1
feat/testcase-search
feat/review-submit
feat/review-decide
feat/suite-approved-item
feat/suite-run
feat/audit-view
```

Mỗi PR gồm migration + API + test + UI nếu applicable.
