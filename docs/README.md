# Scenario Forge — Thiết kế Luồng B

Bộ tài liệu này mô tả kiến trúc **Quản lý, phê duyệt, tìm kiếm** cho Scenario Forge theo hướng Clean Architecture, với stack chính:

- Frontend: Next.js (App Router, TypeScript)
- Backend: FastAPI (Python)
- Database: PostgreSQL + pgvector cho semantic search
- Queue: Redis
- Object storage: MinIO (S3-compatible) qua Backend `StoragePort`
- Deployment: Docker / Docker Compose, mỗi service một container
- Execution integration: Backend quản lý batch job; Execution Worker/ScenarioRunner được coi là hệ thống ngoài và trả kết quả về Backend

## Mục tiêu Luồng B

1. Đăng ký/đăng nhập và RBAC theo Creator, Reviewer, Admin.
2. Test case có metadata, file `.xosc`, tag, người tạo và lịch sử phiên bản.
3. Run Result gắn đúng vào từng **test case version**.
4. Tìm kiếm/lọc theo map, adversary, environment, danger level, status, creator; hỗ trợ semantic/RAG retrieval bằng pgvector.
5. Workflow `DRAFT -> IN_REVIEW -> APPROVED | EDIT | REJECTED`, có comment.
6. Chỉ **approved version** mới được thêm vào Test Suite.
7. Test Suite chạy batch và export sang ScenarioRunner.
8. Audit được ai làm gì, trên đối tượng nào, vào lúc nào.

## Quyết định quan trọng

### 1. Approval thuộc về version, không thuộc test case chung

`test_case` là container logic; nội dung thực tế nằm ở `test_case_version`.

- Version đã `IN_REVIEW`, `EDIT`, `APPROVED` hoặc `REJECTED` không sửa nội dung trực tiếp.
- Muốn sửa version đã review thì tạo version mới ở `DRAFT`.
- Test Suite pin trực tiếp `test_case_version_id` để đảm bảo tái lập kết quả.
- Run Result cũng pin `test_case_version_id`.

Điều này tránh tình huống “đã duyệt A nhưng sau đó file bị sửa thành B mà suite vẫn tưởng là A”.

### 2. Quyền dựa trên permission, không dùng role hierarchy

Một user có thể có nhiều role. `Admin` không mặc định có quyền duyệt; chỉ user mang role `Reviewer` mới có permission approve/request-edit/reject. Thiết kế này bám sát tiêu chí nghiệm thu “quyền duyệt chỉ Reviewer có”.

### 3. 5 trường metadata bắt buộc của Luồng B

Tài liệu nguồn nêu test case có 5 trường bắt buộc nhưng chưa ghi rõ tên. Để thiết kế Luồng B có contract cụ thể, bộ tài liệu tạm dùng:

- `map_code`
- `ego_vehicle_code`
- `adversary_type`
- `environment_code`
- `danger_level`

Khi nhóm chốt tên chính thức của 5 trường, thay tên trong DTO/schema và migration tương ứng; kiến trúc versioning, review và Test Suite không đổi.

## Thứ tự đọc

1. [00-scope-flow-b.md](docs/00-scope-flow-b.md)
2. [01-system-architecture.md](docs/01-system-architecture.md)
3. [02-backend-clean-architecture.md](docs/02-backend-clean-architecture.md)
4. [03-frontend-architecture.md](docs/03-frontend-architecture.md)
5. [04-database-design.md](docs/04-database-design.md)
6. [05-api-contract.md](docs/05-api-contract.md)
7. [06-auth-rbac-approval.md](docs/06-auth-rbac-approval.md)
8. [07-test-suite-and-run.md](docs/07-test-suite-and-run.md)
9. [08-search-audit-storage.md](docs/08-search-audit-storage.md)
10. [09-docker-deployment.md](docs/09-docker-deployment.md)
11. [10-testing-and-acceptance.md](docs/10-testing-and-acceptance.md)
12. [11-implementation-roadmap.md](docs/11-implementation-roadmap.md)
13. [12-sequence-diagrams.md](docs/12-sequence-diagrams.md)
14. [13-rag-search-filter.md](docs/13-rag-search-filter.md)
15. [14-mock-test-case-data.md](docs/14-mock-test-case-data.md)
16. [15-minio-object-storage.md](docs/15-minio-object-storage.md)
17. [16-phan-quyen-va-man-hinh.md](docs/16-phan-quyen-va-man-hinh.md) — ai được làm gì, ở màn nào
18. [17-deploy-render.md](docs/17-deploy-render.md) — triển khai lên Render
19. [18-worker-carla-vps.md](docs/18-worker-carla-vps.md) — worker CARLA, bảo mật và kết nối với production trên VPS (thiết kế)
20. [19-agent-sinh-kich-ban.md](docs/19-agent-sinh-kich-ban.md) — Agent sinh kịch bản từ prompt dựa trên dữ liệu CARLA (mặc định / CARLA của người dùng)
21. [20-kich-ban-khong-version.md](docs/20-kich-ban-khong-version.md) — kịch bản không có version: sinh một lần, sửa ghi đè, khóa khi đưa vào chạy, duyệt nhanh và hàng loạt (đã triển khai)
22. [21-simulator-runner-bridge.md](docs/21-simulator-runner-bridge.md) — Simulator Runner qua Bridge: BE gửi `run.assign`, Bridge trả `job.completed` / `job.failed` (backend đã triển khai)

## Tìm kiếm semantic/RAG

Search được thiết kế theo hướng hybrid: filter chính xác bằng PostgreSQL, semantic retrieval bằng pgvector. Mock data và expected search behavior nằm ở `docs/14-mock-test-case-data.md`.

## Clean Architecture áp dụng trong dự án

Quy tắc xuyên suốt:

```text
Framework / Infrastructure  --->  Interface Adapters  --->  Application  --->  Domain
       ngoài cùng                                                      trong cùng
```

Dependency chỉ hướng vào trong. Domain không import FastAPI, SQLAlchemy, Redis, pgvector, MinIO SDK hay Next.js.

Backend sẽ tổ chức theo feature + layer để vừa “screaming architecture” vừa tránh tạo một thư mục `services/` khổng lồ.
