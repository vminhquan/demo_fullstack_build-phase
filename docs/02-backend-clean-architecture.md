# 02 — Backend FastAPI theo Clean Architecture

## 1. Mục tiêu

FastAPI chỉ là delivery mechanism. Business logic không nằm trong router, ORM model hoặc Pydantic schema.

## 2. Folder structure đề xuất

```text
backend/
├── app/
│   ├── main.py
│   ├── bootstrap.py                 # dependency wiring
│   ├── shared/
│   │   ├── domain/
│   │   │   ├── entity.py
│   │   │   ├── value_object.py
│   │   │   ├── exceptions.py
│   │   │   └── events.py
│   │   ├── application/
│   │   │   ├── clock.py
│   │   │   ├── unit_of_work.py
│   │   │   └── pagination.py
│   │   └── infrastructure/
│   │       ├── db/
│   │       ├── storage/          # MinIO/S3-compatible adapter
│   │       ├── queue/
│   │       └── security/
│   └── modules/
│       ├── identity/
│       │   ├── domain/
│       │   ├── application/
│       │   ├── adapters/
│       │   └── infrastructure/
│       ├── testcase/
│       │   ├── domain/
│       │   │   ├── entities.py
│       │   │   ├── enums.py
│       │   │   ├── policies.py
│       │   │   └── repositories.py
│       │   ├── application/
│       │   │   ├── commands/
│       │   │   ├── queries/
│       │   │   ├── dto.py
│       │   │   └── ports.py
│       │   ├── adapters/
│       │   │   └── http/
│       │   │       ├── router.py
│       │   │       └── schemas.py
│       │   └── infrastructure/
│       │       ├── persistence/
│       │       │   ├── orm.py
│       │       │   ├── mapper.py
│       │       │   └── repository.py
│       │       └── storage.py        # MinioStorageAdapter
│       ├── review/...
│       ├── testsuite/...
│       ├── execution/...
│       └── audit/...
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── pyproject.toml
└── Dockerfile
```

### Vì sao feature + layer?

Không chọn kiểu:

```text
models/
services/
routers/
repositories/
```

cho toàn ứng dụng vì khi dự án lớn, code của review/testsuite/execution bị trộn. Cấu trúc theo module giúp nhìn folder là thấy nghiệp vụ của Scenario Forge.

## 3. Dependency rule

```text
adapters/http ----> application ----> domain
infrastructure ---> application/domain ports
```

Cấm:

```python
# domain/entities.py
from fastapi import HTTPException       # sai
from sqlalchemy.orm import Mapped       # sai
from redis.asyncio import Redis         # sai
```

Domain dùng Python thuần.

## 4. Domain model cốt lõi

```python
class TestCaseVersion:
    id: UUID
    test_case_id: UUID
    version_no: int
    status: VersionStatus
    map_code: str
    ego_vehicle_code: str
    adversary_type: str
    environment_code: str
    danger_level: DangerLevel
    xosc_artifact_id: UUID
    created_by: UUID

    def submit_for_review(self): ...
    def approve(self): ...
    def reject(self): ...
```

Rule domain đáng giữ trong entity/policy:

```python
def ensure_editable(version):
    if version.status != VersionStatus.DRAFT:
        raise VersionNotEditable()

def ensure_suite_eligible(version):
    if version.status != VersionStatus.APPROVED:
        raise VersionNotApproved()
```

## 5. Repository là port

Domain/Application định nghĩa interface, Infrastructure implement.

```python
from typing import Protocol

class TestCaseVersionRepository(Protocol):
    async def get(self, version_id: UUID) -> TestCaseVersion | None: ...
    async def add(self, version: TestCaseVersion) -> None: ...
    async def next_version_no(self, test_case_id: UUID) -> int: ...
```

SQLAlchemy repository:

```python
class SqlAlchemyTestCaseVersionRepository(TestCaseVersionRepository):
    def __init__(self, session):
        self.session = session

    async def get(self, version_id):
        row = await self.session.get(TestCaseVersionModel, version_id)
        return TestCaseMapper.to_domain(row) if row else None
```

## 6. Use case là nơi orchestration

Ví dụ approve:

```python
class ApproveVersion:
    def __init__(self, versions, reviews, audit, uow, authorizer):
        self.versions = versions
        self.reviews = reviews
        self.audit = audit
        self.uow = uow
        self.authorizer = authorizer

    async def execute(self, actor, review_id, comment=None):
        self.authorizer.require(actor, "review:decide")

        review = await self.reviews.get_for_update(review_id)
        version = await self.versions.get_for_update(review.version_id)

        review.approve(actor.id, comment)
        version.approve()

        await self.audit.record(...)
        await self.uow.commit()
```

Router chỉ parse HTTP và gọi use case:

```python
@router.post("/reviews/{review_id}/approve")
async def approve_review(
    review_id: UUID,
    body: ReviewDecisionRequest,
    actor=Depends(current_user),
    uc=Depends(get_approve_version),
):
    result = await uc.execute(actor, review_id, body.comment)
    return ReviewResponse.from_domain(result)
```

## 7. Command/query split vừa đủ

Không cần full CQRS infrastructure. Chỉ tách code theo ý nghĩa:

```text
application/commands/
  create_test_case.py
  create_version.py
  submit_review.py
  approve_version.py
  reject_version.py
  add_suite_item.py
  run_suite.py

application/queries/
  search_test_cases.py
  get_test_case_detail.py
  list_review_queue.py
  get_run_result.py
  list_audit_logs.py
```

Search query có thể đọc projection/SQL trực tiếp qua QueryRepository, không buộc hydrate aggregate cho từng row.

## 8. Unit of Work

Mỗi command quan trọng chạy trong một transaction:

```text
BEGIN
  lock row cần thiết
  validate domain rule
  persist state
  insert audit log
COMMIT
```

MinIO không nằm trong DB transaction. Dùng flow an toàn:

1. stream file, validate type/size và tính `sha256`;
2. tạo `artifact_id` + immutable `object_key`;
3. upload object vào MinIO;
4. DB transaction tạo artifact metadata và cập nhật version/result;
5. nếu DB transaction fail, enqueue/attempt `delete_object` bằng compensating action;
6. nếu MinIO upload fail thì không commit metadata artifact.

Với file rất lớn có thể dùng presigned upload: Backend tạo pending artifact/upload intent, cấp URL ngắn hạn, client upload MinIO, rồi gọi finalize để Backend `stat_object`, verify và commit metadata.

Artifact từ Worker Luồng A đi qua integration endpoint/stream của Backend; Luồng B persist file metadata và RunResult.

## 9. Error mapping

Domain error -> HTTP ở adapter:

```text
NotFound                 -> 404
Forbidden                -> 403
InvalidTransition        -> 409
VersionNotApproved       -> 409
DuplicateVersion         -> 409
ValidationError          -> 422
StorageUnavailable       -> 503
QueueUnavailable         -> 503
```

Không `raise HTTPException` trong domain/application.

## 10. Dependency injection

`bootstrap.py` là composition root:

```text
FastAPI dependency
  -> UseCase
      -> Repository interface -> SQLAlchemyRepository
      -> Storage interface    -> MinioStorageAdapter
      -> Queue interface      -> RedisQueue
      -> Audit interface      -> PostgreSQLAuditRepository
```

Vì dependency được inject, unit test có thể dùng in-memory repository và fake queue/storage.
