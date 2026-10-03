# 00 — Phạm vi Luồng B

## In scope

Luồng B chỉ thiết kế các chức năng quản lý và vận hành lifecycle test case:

1. Đăng ký / đăng nhập / phân quyền Creator, Reviewer, Admin.
2. Lưu Test Case, metadata, tag, file `.xosc`, lịch sử phiên bản.
3. Lưu Run Result gắn với đúng `test_case_version_id`.
4. Tìm kiếm và lọc theo map, adversary, environment, danger level, status, creator; bổ sung semantic/RAG retrieval.
5. Workflow `DRAFT -> IN_REVIEW -> APPROVED | EDIT | REJECTED`, comment review.
6. Test Suite chỉ nhận version đã `APPROVED`.
7. Batch run + export package sang execution runtime / ScenarioRunner.
8. Audit log toàn bộ hành động quan trọng.
9. Docker hóa frontend, backend, PostgreSQL + pgvector, Redis và MinIO; artifact `.xosc`, log, ảnh/video được lưu trong MinIO.

## Out of scope

Không thiết kế trong bộ tài liệu này:

- Agent sinh scenario.
- Prompt / tool calling / self-repair.
- CARLA topology/catalog generation.
- Cách spawn vehicle/adversary.
- Logic mô phỏng và tiêu chí vật lý chi tiết.
- Nội bộ ScenarioRunner.

Execution runtime chỉ được nhìn như external adapter có contract:

```text
Backend -> RunRequest -> Worker Luồng A
Worker Luồng A -> status / RunResult / Artifact -> Backend
```

## Boundary quan trọng

Luồng B sở hữu dữ liệu và trạng thái nghiệp vụ. Worker của Luồng A không được ghi trực tiếp PostgreSQL; mọi mutation đi qua Backend API để giữ RBAC, audit và invariant. Kết nối Worker/WebSocket chỉ là integration boundary cần verify với Luồng A.
