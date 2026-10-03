# 12 — Sequence Diagrams cho Demo Luồng B

## 1. Creator tạo test case và submit review

```mermaid
sequenceDiagram
    actor A as Creator
    participant UI as Next.js
    participant API as FastAPI
    participant DB as PostgreSQL
    participant S as MinIO

    A->>UI: Create test case
    UI->>API: POST /test-cases
    API->>DB: insert test_case
    API-->>UI: case_id

    A->>UI: Fill 5 fields + upload XOSC
    UI->>API: POST /test-cases/{id}/versions
    API->>DB: create DRAFT version
    UI->>API: POST /versions/{id}/xosc
    API->>S: put XOSC object via MinioStorageAdapter
    API->>DB: artifact metadata + sha256

    A->>UI: Submit review
    UI->>API: POST /versions/{id}/submit-review
    API->>DB: DRAFT -> IN_REVIEW + review + audit
    API-->>UI: review_id
```

## 2. Reviewer approve

```mermaid
sequenceDiagram
    actor R as Reviewer
    participant UI as Next.js
    participant API as FastAPI
    participant DB as PostgreSQL

    R->>UI: Open review queue
    UI->>API: GET /reviews?open=true
    API->>DB: query open reviews
    API-->>UI: review list

    R->>UI: Approve + comment
    UI->>API: POST /reviews/{id}/approve
    API->>API: require review:decide
    API->>DB: lock review + version
    API->>DB: version -> APPROVED
    API->>DB: decision/comment + audit
    API-->>UI: approved
```

## 3. Reviewer reject, Creator tạo version mới

```mermaid
sequenceDiagram
    actor R as Reviewer
    actor A as Creator
    participant UI as Next.js
    participant API as FastAPI
    participant DB as PostgreSQL

    R->>API: POST /reviews/{id}/reject {comment}
    API->>DB: v1 -> REJECTED + decision + audit

    A->>UI: Read reject comment
    A->>API: POST /versions/{v1}/clone
    API->>DB: create v2 DRAFT copied from v1
    API-->>A: v2

    A->>API: PATCH v2 metadata / replace XOSC
    A->>API: POST v2/submit-review
    API->>DB: v2 -> IN_REVIEW
```

## 4. Add approved version vào suite

```mermaid
sequenceDiagram
    actor U as User
    participant UI as Next.js
    participant API as FastAPI
    participant DB as PostgreSQL

    U->>UI: Add version to suite
    UI->>API: POST /test-suites/{id}/items
    API->>DB: read version
    alt APPROVED
      API->>DB: insert suite_item + audit
      API-->>UI: 201 Created
    else not approved
      API-->>UI: 409 VERSION_NOT_APPROVED
    end
```

## 5. Run batch và xem kết quả

```mermaid
sequenceDiagram
    actor U as User
    participant UI as Next.js
    participant API as FastAPI
    participant DB as PostgreSQL
    participant Q as Redis
    participant W as Worker Luồng A
    participant C as ScenarioRunner

    U->>UI: Run suite
    UI->>API: POST /test-suites/{id}/runs
    API->>DB: snapshot items + create jobs
    API->>Q: enqueue job IDs
    API-->>UI: 202 suite_run_id

    loop each job
      W->>API: claim/heartbeat
      API-->>W: XOSC job
      W->>C: run
      C-->>W: pass/fail/error + log
      W->>API: complete + artifacts
      API->>DB: result + audit
    end

    UI->>API: GET /suite-runs/{id}
    API-->>UI: progress + results
```

## 6. Traceability chain

Một kết quả bất kỳ phải lần ngược được:

```text
RunResult
  -> RunJob
  -> TestCaseVersion
  -> TestCase
  -> XOSC Artifact (SHA256)
  -> ReviewRequest / Reviewer / Comment
  -> SuiteRun / TestSuite
  -> AuditLog events
```

Đây là chain chính để chứng minh tiêu chí “truy vết được ai làm gì với mọi test case”.


## 7. Hybrid semantic search

```mermaid
sequenceDiagram
    actor U as User
    participant UI as Next.js
    participant API as FastAPI
    participant E as EmbeddingPort
    participant DB as PostgreSQL + pgvector

    U->>UI: query + filters
    UI->>API: POST /test-cases/search
    API->>API: validate SearchCriteria + RBAC scope
    API->>E: embed(query)
    E-->>API: query vector
    API->>DB: WHERE filters + vector similarity
    DB-->>API: ranked TestCaseSummary
    API-->>UI: results + score/matched_by
```

Nếu embedding unavailable, API fallback về keyword + structured filter theo contract.
