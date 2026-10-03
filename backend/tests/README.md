# Test strategy

`unit/` giữ policy/domain rule không cần PostgreSQL: quyền reviewer, DRAFT-only edit, APPROVED-only suite item và lý do reject.

Các integration test tiếp theo phải chạy PostgreSQL + pgvector thật (không dùng SQLite) để kiểm tra migration, enum/JSONB, row locking, trigger `trg_suite_item_approved`, MinIO artifact lifecycle và worker completion idempotency.
