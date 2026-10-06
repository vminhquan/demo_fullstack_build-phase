"""Flat test cases: no versions, no review requests (docs/20-kich-ban-khong-version.md).

Every test_case_versions row becomes a test case: the table is renamed to test_cases, so ids and the
id sequence are kept and every foreign key that pointed at a version (suite items, run jobs/results,
search documents, tags, scenario generations) stays valid after a column rename.

- Title/description/builder link come from the old parent test case. The newest version of a case keeps
  its case_key; older versions get "<case_key>-v<version_no>".
- Status: DRAFT/IN_REVIEW -> PENDING, APPROVED -> APPROVED, REJECTED/EDIT -> REJECTED.
- Decided review requests become test_case_decisions (EDIT -> REJECTED); comments are dropped.
- A case already used by a run job is locked at the first job's time.

Revision ID: 0007_flat_test_cases
Revises: 0006_bridges
Create Date: 2026-10-06
"""
import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = '0007_flat_test_cases'
down_revision = '0006_bridges'
branch_labels = None
depends_on = None


def _config_sha256(row) -> str:
    # Frozen copy of app.modules.testcase.service.config_sha256 (migrations must not import app code).
    payload = {
        "title": row.title,
        "description": row.description or "",
        "map_code": row.map_code,
        "ego_vehicle_code": row.ego_vehicle_code,
        "adversary_type": row.adversary_type,
        "environment_code": row.environment_code,
        "danger_level": row.danger_level,
        "scenario_input": row.scenario_input or {},
        "xosc_sha256": row.xosc_sha256 or "",
        "tags": sorted(row.tags or []),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def upgrade():
    bind = op.get_bind()
    op.execute("CREATE TYPE test_case_status AS ENUM ('PENDING', 'APPROVED', 'REJECTED', 'DISCARDED')")
    op.execute("CREATE TYPE test_case_decision AS ENUM ('APPROVED', 'REJECTED')")

    # The suite trigger reads test_case_versions; recreated at the end for the new table. The function is
    # replaced, not dropped: the archived legacy_uuid_0004 schema may still have a trigger using it.
    op.execute("DROP TRIGGER IF EXISTS trg_suite_item_approved ON test_suite_items")

    # 1. Fold the parent test case into every version row.
    op.execute("""
        ALTER TABLE test_case_versions
            ADD COLUMN case_key VARCHAR(64),
            ADD COLUMN title VARCHAR(300),
            ADD COLUMN description TEXT,
            ADD COLUMN new_status test_case_status,
            ADD COLUMN revision BIGINT NOT NULL DEFAULT 1,
            ADD COLUMN config_sha256 VARCHAR(64) NOT NULL DEFAULT '',
            ADD COLUMN xosc_sha256 VARCHAR(64),
            ADD COLUMN last_edited_by BIGINT REFERENCES users (id),
            ADD COLUMN last_edited_at TIMESTAMPTZ,
            ADD COLUMN discarded_at TIMESTAMPTZ,
            ADD COLUMN locked_at TIMESTAMPTZ,
            ADD COLUMN archived_at TIMESTAMPTZ,
            ADD COLUMN builder_session_id BIGINT,
            ADD COLUMN builder_variant_no INTEGER,
            ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    """)
    op.execute("""
        UPDATE test_case_versions v
        SET title = c.title,
            description = c.description,
            archived_at = c.archived_at,
            case_key = CASE WHEN v.version_no = latest.max_no THEN c.case_key ELSE c.case_key || '-v' || v.version_no END,
            builder_session_id = CASE WHEN v.version_no = latest.max_no THEN c.builder_session_id END,
            builder_variant_no = CASE WHEN v.version_no = latest.max_no THEN c.builder_variant_no END,
            new_status = CASE v.status::text
                WHEN 'APPROVED' THEN 'APPROVED'::test_case_status
                WHEN 'REJECTED' THEN 'REJECTED'::test_case_status
                WHEN 'EDIT' THEN 'REJECTED'::test_case_status
                ELSE 'PENDING'::test_case_status END,
            updated_at = COALESCE(v.decided_at, v.submitted_at, v.created_at),
            locked_at = (SELECT min(j.created_at) FROM run_jobs j WHERE j.test_case_version_id = v.id)
        FROM test_cases c,
             (SELECT test_case_id, max(version_no) AS max_no FROM test_case_versions GROUP BY test_case_id) latest
        WHERE c.id = v.test_case_id AND latest.test_case_id = v.test_case_id
    """)

    # 2. Decisions from review requests that reached a decision.
    op.execute("""
        CREATE TABLE test_case_decisions (
            project_id BIGINT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            test_case_id BIGINT NOT NULL,
            decision test_case_decision NOT NULL,
            revision BIGINT NOT NULL,
            config_sha256 VARCHAR(64) NOT NULL,
            decided_by BIGINT NOT NULL REFERENCES users (id),
            undone_at TIMESTAMPTZ,
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        INSERT INTO test_case_decisions (project_id, test_case_id, decision, revision, config_sha256, decided_by, created_at)
        SELECT r.project_id, r.version_id,
               CASE r.decision::text WHEN 'APPROVED' THEN 'APPROVED'::test_case_decision ELSE 'REJECTED'::test_case_decision END,
               1, '', r.resolved_by, r.resolved_at
        FROM review_requests r
        WHERE r.decision IS NOT NULL AND r.resolved_by IS NOT NULL
    """)

    # Audit rows keep pointing at the right case: old case ids -> the case's newest version (now a case id),
    # version rows are already case ids, review rows -> the reviewed version.
    op.execute("""
        UPDATE audit_logs l SET entity_id = latest.id
        FROM (SELECT DISTINCT ON (test_case_id) test_case_id, id FROM test_case_versions
              ORDER BY test_case_id, version_no DESC) latest
        WHERE l.entity_type = 'TEST_CASE' AND l.entity_id = latest.test_case_id
    """)
    op.execute("""
        UPDATE audit_logs l SET entity_type = 'TEST_CASE', entity_id = r.version_id
        FROM review_requests r
        WHERE l.entity_type = 'REVIEW_REQUEST' AND l.entity_id = r.id
    """)
    op.execute("UPDATE audit_logs SET entity_type = 'TEST_CASE' WHERE entity_type = 'TEST_CASE_VERSION'")

    # 3. Drop what only versions/reviews needed, then the old parent table.
    op.execute("DROP TABLE review_comments")
    op.execute("DROP TABLE review_requests")
    op.execute("""
        ALTER TABLE test_case_versions
            DROP CONSTRAINT uq_test_case_version_no,
            DROP COLUMN test_case_id,
            DROP COLUMN version_no,
            DROP COLUMN submitted_at,
            DROP COLUMN change_note,
            DROP COLUMN status
    """)
    op.execute("ALTER TABLE test_case_versions RENAME COLUMN new_status TO status")
    op.execute("""
        ALTER TABLE test_case_versions
            ALTER COLUMN status SET NOT NULL,
            ALTER COLUMN case_key SET NOT NULL,
            ALTER COLUMN title SET NOT NULL
    """)
    op.execute("DROP TABLE test_cases")

    # 4. The version table becomes test_cases (ids and sequence kept).
    op.execute("ALTER TABLE test_case_versions RENAME TO test_cases")
    op.execute("ALTER SEQUENCE test_case_versions_id_seq RENAME TO test_cases_id_seq")
    op.execute("ALTER TABLE test_cases RENAME CONSTRAINT test_case_versions_pkey TO test_cases_pkey")
    op.execute("ALTER TABLE test_cases RENAME CONSTRAINT fk_tcv_catalog_snapshot TO fk_test_case_catalog_snapshot")
    op.execute("ALTER INDEX idx_tcv_project_map RENAME TO idx_test_cases_project_map")
    # The old status index went away with the old status column.
    op.execute("CREATE INDEX idx_test_cases_project_status ON test_cases (project_id, status)")
    op.execute("ALTER INDEX idx_tcv_project_danger RENAME TO idx_test_cases_project_danger")
    op.execute("ALTER INDEX ix_test_case_versions_project_id RENAME TO ix_test_cases_project_id")
    op.execute("""
        ALTER TABLE test_cases
            ADD CONSTRAINT uq_test_case_project_key UNIQUE (project_id, case_key),
            ADD CONSTRAINT fk_test_cases_builder_session FOREIGN KEY (builder_session_id)
                REFERENCES builder_sessions (id) ON DELETE SET NULL
    """)
    op.execute("CREATE INDEX idx_test_cases_project_updated ON test_cases (project_id, updated_at)")
    op.execute("CREATE INDEX ix_test_cases_created_by ON test_cases (created_by)")
    op.execute("CREATE INDEX ix_test_cases_builder_session_id ON test_cases (builder_session_id)")
    op.execute("""
        ALTER TABLE test_case_decisions
            ADD CONSTRAINT test_case_decisions_test_case_id_fkey FOREIGN KEY (test_case_id)
                REFERENCES test_cases (id) ON DELETE CASCADE
    """)
    op.execute("CREATE INDEX ix_test_case_decisions_project_id ON test_case_decisions (project_id)")
    op.execute("CREATE INDEX ix_test_case_decisions_test_case_id ON test_case_decisions (test_case_id)")
    op.execute("CREATE INDEX idx_tc_decisions_project_created ON test_case_decisions (project_id, created_at)")

    # 5. Columns that pointed at a version now point at a test case.
    op.execute("ALTER TABLE test_suite_items RENAME COLUMN test_case_version_id TO test_case_id")
    op.execute("ALTER TABLE run_jobs RENAME COLUMN test_case_version_id TO test_case_id")
    op.execute("ALTER TABLE run_results RENAME COLUMN test_case_version_id TO test_case_id")
    op.execute("""
        ALTER TABLE run_results
            ADD COLUMN revision BIGINT,
            ADD COLUMN config_sha256 VARCHAR(64),
            ADD COLUMN xosc_sha256 VARCHAR(64)
    """)
    op.execute("ALTER TABLE test_case_search_documents RENAME COLUMN version_id TO test_case_id")
    op.execute("""
        ALTER TABLE test_case_search_documents
            RENAME CONSTRAINT test_case_search_documents_version_id_key TO test_case_search_documents_test_case_id_key
    """)
    op.execute("ALTER TABLE test_case_version_tags RENAME TO test_case_tags")
    op.execute("ALTER TABLE test_case_tags RENAME COLUMN version_id TO test_case_id")
    op.execute("ALTER TABLE test_case_tags RENAME CONSTRAINT test_case_version_tags_pkey TO test_case_tags_pkey")
    op.execute("ALTER TABLE scenario_generations RENAME COLUMN accepted_version_id TO accepted_test_case_id")

    op.execute("UPDATE test_cases c SET xosc_sha256 = a.sha256 FROM artifacts a WHERE a.id = c.xosc_artifact_id")

    # 6. Config hashes (needs tags and the XOSC hash), then copy them onto the migrated decisions.
    rows = bind.execute(sa.text("""
        SELECT c.id, c.title, c.description, c.map_code, c.ego_vehicle_code, c.adversary_type,
               c.environment_code, c.danger_level::text AS danger_level, c.scenario_input, a.sha256 AS xosc_sha256,
               COALESCE((SELECT array_agg(t.name) FROM test_case_tags tt JOIN tags t ON t.id = tt.tag_id
                         WHERE tt.test_case_id = c.id), ARRAY[]::varchar[]) AS tags
        FROM test_cases c LEFT JOIN artifacts a ON a.id = c.xosc_artifact_id
    """)).all()
    for row in rows:
        bind.execute(sa.text("UPDATE test_cases SET config_sha256 = :hash WHERE id = :id"),
                     {"hash": _config_sha256(row), "id": row.id})
    op.execute("UPDATE test_case_decisions d SET config_sha256 = c.config_sha256 FROM test_cases c WHERE c.id = d.test_case_id")

    # 7. Suite items may only pin APPROVED cases of their own project.
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_approved_suite_item()
        RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM test_cases
            WHERE id = NEW.test_case_id
              AND project_id = NEW.project_id
              AND status = 'APPROVED'
          ) THEN
            RAISE EXCEPTION 'Only APPROVED test cases in the same project can be added to a suite';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_suite_item_approved
        BEFORE INSERT ON test_suite_items
        FOR EACH ROW EXECUTE FUNCTION enforce_approved_suite_item();
    """)

    # The archived legacy_uuid_0004 schema may still use these enums; keep a type in that case.
    for name in ("version_status", "review_decision", "comment_type"):
        op.execute(f"""
            DO $$
            BEGIN
              DROP TYPE IF EXISTS {name};
            EXCEPTION WHEN dependent_objects_still_exist THEN
              NULL;
            END $$;
        """)


def downgrade():
    # Versions cannot be rebuilt: older versions became separate cases and review comments were dropped.
    raise NotImplementedError("0007_flat_test_cases is irreversible")
