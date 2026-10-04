# 03 — Frontend Next.js

## 1. Nguyên tắc

Frontend không chứa authorization rule mang tính bảo mật. UI có thể ẩn nút theo permission, nhưng Backend luôn là nơi quyết định cuối cùng.

## 2. Cấu trúc đề xuất

```text
frontend/
├── src/
│   ├── app/
│   │   ├── (public)/
│   │   │   ├── login/page.tsx
│   │   │   └── register/page.tsx
│   │   ├── (protected)/
│   │   │   ├── layout.tsx
│   │   │   ├── dashboard/page.tsx
│   │   │   ├── test-cases/              # Test Case Builder (phiên agent, mock)
│   │   │   │   ├── page.tsx
│   │   │   │   ├── create/page.tsx
│   │   │   │   ├── generate/page.tsx
│   │   │   │   ├── new/page.tsx         # redirect -> /test-cases/create
│   │   │   │   └── [id]/page.tsx
│   │   │   ├── scenarios/               # Danh mục test case (catalog)
│   │   │   │   ├── page.tsx
│   │   │   │   └── [id]/
│   │   │   │       ├── page.tsx
│   │   │   │       └── versions/[versionId]/page.tsx
│   │   │   ├── reviews/page.tsx
│   │   │   ├── suites/
│   │   │   │   ├── page.tsx
│   │   │   │   └── [id]/page.tsx
│   │   │   ├── runs/[id]/page.tsx
│   │   │   └── audit/page.tsx
│   │   └── api/                         # optional BFF/proxy helpers only
│   ├── features/
│   │   ├── auth/
│   │   │   ├── domain/
│   │   │   ├── application/
│   │   │   ├── api/
│   │   │   └── ui/
│   │   ├── testcase/
│   │   ├── review/
│   │   ├── testsuite/
│   │   ├── execution/
│   │   └── audit/
│   ├── shared/
│   │   ├── api/http-client.ts
│   │   ├── auth/session.ts
│   │   ├── components/
│   │   ├── config/
│   │   └── utils/
│   └── middleware.ts
├── next.config.ts
├── package.json
└── Dockerfile
```

## 3. Layer nhẹ cho frontend

Không cần copy nguyên Clean Architecture của backend. Dùng 4 vùng:

```text
ui -> application hooks/actions -> domain types/rules -> api adapter
```

Ví dụ feature review:

```text
features/review/
├── domain/
│   ├── review.ts
│   └── permissions.ts
├── application/
│   ├── use-review-queue.ts
│   └── use-review-decision.ts
├── api/
│   └── review-api.ts
└── ui/
    ├── review-table.tsx
    ├── review-diff.tsx
    └── decision-dialog.tsx
```

## 4. Routes/screens cho demo nghiệm thu

### `/test-cases` — Test Case Builder (phiên agent)

Một phiên = một lần gửi đầu vào cho Agent, sau đó chỉ duyệt kết quả (không có hội thoại). Danh sách phiên mẫu ở `features/agent-sessions/mock-data.ts`; phiên tạo mới và quyết định duyệt nằm trong `session-store.ts` (bộ nhớ + sessionStorage) đến khi Backend có API phiên.

- `/test-cases`: danh sách phiên (tiêu đề + mô tả + tag, bối cảnh, số kịch bản đang sinh/chờ duyệt/đã duyệt/loại, người tạo, cập nhật); tìm kiếm; nút "Phiên mới". Không có cột trạng thái.
- `/test-cases/create`: form full width (tiêu đề, mô tả, số test case 1–10, 5 metadata, thẻ, ghi chú). Bấm "Tạo test case" → tạo phiên, chạy Agent nền (`POST /scenario-generations` + `accept`, tối đa 3 song song) và điều hướng ngay sang `/test-cases/[id]`.
- `/test-cases/[id]`: 2 cột. Trái: đúng các input của form tạo, chỉ xem. Phải: kịch bản đã sinh theo tab trạng thái (đang sinh, chờ duyệt, đã duyệt, từ chối); người có `review:decide` duyệt/từ chối kèm ghi chú.
- `/test-cases/new`: chuyển hướng sang `/test-cases/create`.

### `/scenarios` — Danh mục test case

- Search text.
- Filter map/adversary/environment/danger/status/creator/tag.
- Sort created/updated.
- Pagination.
- Badge version + status.
- Action tùy permission.

URL giữ filter để share/reload:

```text
/scenarios?map=Town05&adversary=pedestrian&danger=HIGH&status=APPROVED&page=1
```

### `/scenarios/[id]`

- Logical test case information.
- Latest version.
- Version history timeline.
- Review status.
- Run history.

### `/scenarios/[id]/versions/[versionId]`

- Metadata snapshot.
- XOSC viewer/download.
- checksum.
- Review comments.
- Run results.
- “Create new version from this version”.

### `/reviews`

Reviewer inbox:

- only `IN_REVIEW` by default;
- diff metadata between current and previous version;
- XOSC diff/text view;
- comment;
- Approve;
- Reject.

### `/suites/[id]`

- suite metadata;
- items pinned by version;
- add only from approved search results;
- reorder;
- export;
- run batch;
- aggregate run progress.

### `/runs/[id]`

- state timeline `QUEUED -> CLAIMED -> RUNNING -> COMPLETED/FAILED`;
- pass/fail/error;
- metrics;
- log viewer;
- artifact links.

### `/audit`

Admin only:

- actor;
- action;
- entity type/id;
- time range;
- request id;
- expandable before/after JSON.

## 5. Data fetching

Khuyến nghị:

- Server Component cho page shell/initial auth where useful.
- TanStack Query cho list/filter/review/run polling vì có interaction nhiều.
- React Hook Form + Zod cho form.
- URLSearchParams là source of truth cho filters.

Query keys:

```ts
['test-cases', filters]
['test-case', id]
['test-case-version', versionId]
['review-queue', filters]
['suite', suiteId]
['suite-run', runId]
['audit', filters]
```

Sau mutation approve/request-edit/reject:

```text
invalidate review queue
invalidate version detail
invalidate test case list
```

## 6. Authentication trên web

Ưu tiên:

- FastAPI phát access token ngắn hạn.
- Refresh token lưu HttpOnly + Secure cookie, rotate mỗi lần refresh.
- Access token giữ memory phía client hoặc dùng cookie cùng CSRF protection.
- Không lưu refresh token trong `localStorage`.

Với Docker local, Next.js proxy `/api/*` sang `backend:8000` để browser thấy cùng origin.

## 7. Authorization UX

Backend trả `/auth/me`:

```json
{
  "id": "...",
  "display_name": "Reviewer A",
  "roles": ["REVIEWER"],
  "permissions": ["testcase:read", "review:read", "review:decide"]
}
```

UI dùng permission:

```tsx
{can('review:decide') && <ApproveButton />}
```

Không dùng:

```tsx
if (role === 'ADMIN') allowEverything(); // không phù hợp tiêu chí chỉ Reviewer duyệt
```

## 8. Run status realtime

MVP: polling 2–5 giây trên trang run.

Sau đó có thể nâng lên SSE/WebSocket:

```text
GET /api/v1/suite-runs/{id}/events
```

MVP nên ưu tiên polling vì dễ debug và đủ demo.
