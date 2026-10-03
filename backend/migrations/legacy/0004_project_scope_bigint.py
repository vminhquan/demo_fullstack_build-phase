"""Move the single-tenant UUID schema to project-scoped BIGINT identifiers.

The legacy tables are retained in schema ``legacy_uuid_0004`` as an on-database
backup. Existing MinIO object metadata is preserved on artifacts, but binary
content cannot be recovered from PostgreSQL and must be uploaded again.

Revision ID: 0004_project_scope_bigint
Revises: 0003_add_edit_review_status
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op

from app.shared.infrastructure.models import Base

revision = "0004_project_scope_bigint"
down_revision = "0003_add_edit_review_status"
branch_labels = None
depends_on = None

LEGACY_SCHEMA = "legacy_uuid_0004"
OLD_TABLES = [
    "audit_logs",
    "run_result_artifacts",
    "review_comments",
    "test_case_version_tags",
    "test_suite_items",
    "run_results",
    "run_jobs",
    "test_suite_runs",
    "review_requests",
    "test_case_search_documents",
    "test_case_versions",
    "test_suites",
    "test_cases",
    "tags",
    "artifacts",
    "auth_sessions",
    "user_roles",
    "roles",
    "users",
]


def _rows(bind: sa.Connection, query: str, **params: Any) -> list[sa.RowMapping]:
    return list(bind.execute(sa.text(query), params).mappings())


def _create_runtime_objects() -> None:
    op.execute(
        """
        CREATE INDEX idx_tc_search_embedding_hnsw
        ON test_case_search_documents USING hnsw (embedding vector_cosine_ops)
        WHERE embedding_status = 'READY'
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_approved_suite_item()
        RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM test_case_versions
            WHERE id = NEW.test_case_version_id
              AND project_id = NEW.project_id
              AND status = 'APPROVED'
          ) THEN
            RAISE EXCEPTION 'Only APPROVED test case versions in the same project can be added to a suite';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_suite_item_approved
        BEFORE INSERT OR UPDATE ON test_suite_items
        FOR EACH ROW EXECUTE FUNCTION enforce_approved_suite_item()
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    id_type = bind.execute(
        sa.text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'id'
            """
        )
    ).scalar_one_or_none()
    if id_type != "uuid":
        return

    op.execute(f"CREATE SCHEMA IF NOT EXISTS {LEGACY_SCHEMA}")
    for table_name in OLD_TABLES:
        exists = bind.execute(
            sa.text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        ).scalar_one_or_none()
        if exists:
            op.execute(f'ALTER TABLE public."{table_name}" SET SCHEMA {LEGACY_SCHEMA}')

    Base.metadata.create_all(bind)
    op.execute("ALTER TABLE test_case_search_documents ADD COLUMN embedding vector(768)")

    tables = Base.metadata.tables

    def insert_id(table_name: str, values: dict[str, Any]) -> int:
        table = tables[table_name]
        return int(
            bind.execute(table.insert().values(**values).returning(table.c.id)).scalar_one()
        )

    user_map: dict[Any, int] = {}
    legacy_users = _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.users ORDER BY created_at")
    for row in legacy_users:
        user_map[row["id"]] = insert_id(
            "users",
            {
                "email": row["email"],
                "password_hash": row["password_hash"],
                "display_name": row["display_name"],
                "account_status": "ACTIVE" if row["is_active"] else "SUSPENDED",
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )

    # A reset legacy database has no user from whom a default project owner can
    # be derived. The structural conversion is complete at this point; the first
    # registered user will create a project through the normal application flow.
    if not legacy_users:
        _create_runtime_objects()
        return

    role_rows = _rows(
        bind,
        f"""
        SELECT ur.user_id, r.code::text AS code
        FROM {LEGACY_SCHEMA}.user_roles ur
        JOIN {LEGACY_SCHEMA}.roles r ON r.id = ur.role_id
        """,
    )
    roles_by_user: dict[Any, set[str]] = {}
    for row in role_rows:
        roles_by_user.setdefault(row["user_id"], set()).add(row["code"])
    owner_old_id = next(
        (row["id"] for row in legacy_users if "ADMIN" in roles_by_user.get(row["id"], set())),
        legacy_users[0]["id"],
    )
    owner_id = user_map[owner_old_id]
    project_id = insert_id(
        "projects",
        {
            "code": "DEFAULT",
            "name": "Scenario Forge",
            "description": "Migrated single-tenant data",
            "created_by": owner_id,
            "status": "ACTIVE",
        },
    )
    # project_users is created from the current models, so write the role/responsibility shape
    # introduced in 0007 directly. Inactive legacy users are not members any more.
    for row in legacy_users:
        new_user_id = user_map[row["id"]]
        if not row["is_active"] and new_user_id != owner_id:
            continue
        old_roles = roles_by_user.get(row["id"], set())
        if new_user_id == owner_id:
            role, responsibilities = "ADMIN", ["TESTCASE_CREATE", "TESTCASE_REVIEW", "TESTCASE_SELF_REVIEW"]
        else:
            role = "ADMIN" if "ADMIN" in old_roles else "MEMBER"
            responsibilities = [
                code
                for legacy, code in (("CREATOR", "TESTCASE_CREATE"), ("REVIEWER", "TESTCASE_REVIEW"))
                if legacy in old_roles
            ]
        bind.execute(
            tables["project_users"].insert().values(
                project_id=project_id,
                user_id=new_user_id,
                role_code=role,
                added_by=owner_id,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
        for code in responsibilities:
            bind.execute(
                tables["project_user_responsibilities"].insert().values(
                    project_id=project_id,
                    user_id=new_user_id,
                    responsibility_code=code,
                    assigned_by=owner_id,
                )
            )

    artifact_map: dict[Any, int] = {}
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.artifacts ORDER BY created_at"):
        metadata = dict(row["metadata"] or {})
        metadata.update(
            {
                "legacy_storage_provider": row["storage_provider"],
                "legacy_bucket_name": row["bucket_name"],
                "legacy_object_key": row["object_key"],
                "legacy_content_requires_reupload": True,
            }
        )
        kind = row["kind"]
        kind = kind if str(kind) != "RUN_VIDEO" else "OTHER"
        artifact_map[row["id"]] = insert_id(
            "artifacts",
            {
                "project_id": project_id,
                "kind": str(kind),
                "original_name": row["original_name"],
                "content_type": row["content_type"],
                "content": b"",
                "size_bytes": 0,
                "sha256": row["sha256"],
                "created_by": user_map.get(row["created_by"]),
                "deleted_at": row["deleted_at"],
                "metadata": metadata,
                "created_at": row["created_at"],
            },
        )

    case_map: dict[Any, int] = {}
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.test_cases ORDER BY created_at"):
        case_map[row["id"]] = insert_id(
            "test_cases",
            {
                "project_id": project_id,
                "case_key": row["case_key"],
                "title": row["title"],
                "description": row["description"],
                "created_by": user_map[row["created_by"]],
                "archived_at": row["archived_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )

    tag_map: dict[Any, int] = {}
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.tags ORDER BY created_at"):
        tag_map[row["id"]] = insert_id(
            "tags",
            {
                "project_id": project_id,
                "name": row["name"],
                "created_at": row["created_at"],
            },
        )

    version_map: dict[Any, int] = {}
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.test_case_versions ORDER BY created_at"):
        version_map[row["id"]] = insert_id(
            "test_case_versions",
            {
                "project_id": project_id,
                "test_case_id": case_map[row["test_case_id"]],
                "version_no": row["version_no"],
                "status": str(row["status"]),
                "map_code": row["map_code"],
                "ego_vehicle_code": row["ego_vehicle_code"],
                "adversary_type": row["adversary_type"],
                "environment_code": row["environment_code"],
                "danger_level": str(row["danger_level"]),
                "scenario_input": row["scenario_input"] or {},
                "xosc_artifact_id": artifact_map.get(row["xosc_artifact_id"]),
                "change_note": row["change_note"],
                "created_by": user_map[row["created_by"]],
                "submitted_at": row["submitted_at"],
                "decided_at": row["decided_at"],
                "decided_by": user_map.get(row["decided_by"]),
                "created_at": row["created_at"],
            },
        )
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.test_case_version_tags"):
        bind.execute(
            tables["test_case_version_tags"].insert().values(
                version_id=version_map[row["version_id"]],
                tag_id=tag_map[row["tag_id"]],
            )
        )

    review_map: dict[Any, int] = {}
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.review_requests ORDER BY requested_at"):
        review_map[row["id"]] = insert_id(
            "review_requests",
            {
                "project_id": project_id,
                "version_id": version_map[row["version_id"]],
                "requested_by": user_map[row["requested_by"]],
                "requested_at": row["requested_at"],
                "resolved_by": user_map.get(row["resolved_by"]),
                "resolved_at": row["resolved_at"],
                "decision": str(row["decision"]) if row["decision"] else None,
                "created_at": row["created_at"],
            },
        )
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.review_comments ORDER BY created_at"):
        insert_id(
            "review_comments",
            {
                "project_id": project_id,
                "review_request_id": review_map[row["review_request_id"]],
                "creator_id": user_map[row["creator_id"]],
                "body": row["body"],
                "comment_type": str(row["comment_type"]),
                "created_at": row["created_at"],
            },
        )

    suite_map: dict[Any, int] = {}
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.test_suites ORDER BY created_at"):
        suite_map[row["id"]] = insert_id(
            "test_suites",
            {
                "project_id": project_id,
                "name": row["name"],
                "description": row["description"],
                "created_by": user_map[row["created_by"]],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.test_suite_items"):
        bind.execute(
            tables["test_suite_items"].insert().values(
                project_id=project_id,
                suite_id=suite_map[row["suite_id"]],
                test_case_version_id=version_map[row["test_case_version_id"]],
                position=row["position"],
                added_by=user_map[row["added_by"]],
                added_at=row["added_at"],
            )
        )

    suite_run_map: dict[Any, int] = {}
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.test_suite_runs ORDER BY created_at"):
        suite_run_map[row["id"]] = insert_id(
            "test_suite_runs",
            {
                "project_id": project_id,
                "suite_id": suite_map[row["suite_id"]],
                "requested_by": user_map[row["requested_by"]],
                "status": str(row["status"]),
                "total_jobs": row["total_jobs"],
                "completed_jobs": row["completed_jobs"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "created_at": row["created_at"],
            },
        )

    job_map: dict[Any, int] = {}
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.run_jobs ORDER BY created_at"):
        job_map[row["id"]] = insert_id(
            "run_jobs",
            {
                "project_id": project_id,
                "suite_run_id": suite_run_map.get(row["suite_run_id"]),
                "test_case_version_id": version_map[row["test_case_version_id"]],
                "status": str(row["status"]),
                "idempotency_key": row["idempotency_key"],
                "worker_id": row["worker_id"],
                "attempt": row["attempt"],
                "max_attempts": row["max_attempts"],
                "queued_at": row["queued_at"],
                "claimed_at": row["claimed_at"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "error_code": row["error_code"],
                "error_message": row["error_message"],
                "created_at": row["created_at"],
            },
        )

    result_map: dict[Any, int] = {}
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.run_results ORDER BY created_at"):
        result_map[row["id"]] = insert_id(
            "run_results",
            {
                "project_id": project_id,
                "run_job_id": job_map[row["run_job_id"]],
                "test_case_version_id": version_map[row["test_case_version_id"]],
                "verdict": str(row["verdict"]),
                "metrics": row["metrics"] or {},
                "scenario_runner_exit_code": row["scenario_runner_exit_code"],
                "duration_ms": row["duration_ms"],
                "created_at": row["created_at"],
            },
        )
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.run_result_artifacts"):
        bind.execute(
            tables["run_result_artifacts"].insert().values(
                project_id=project_id,
                run_result_id=result_map[row["run_result_id"]],
                artifact_id=artifact_map[row["artifact_id"]],
                role=str(row["role"]),
            )
        )

    auth_session_map: dict[Any, int] = {}
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.auth_sessions ORDER BY created_at"):
        auth_session_map[row["id"]] = insert_id(
            "auth_sessions",
            {
                "user_id": user_map[row["user_id"]],
                "project_id": project_id,
                "refresh_token_hash": row["refresh_token_hash"],
                "expires_at": row["expires_at"],
                "revoked_at": row["revoked_at"],
                "last_used_at": row["last_used_at"],
                "created_at": row["created_at"],
            },
        )

    entity_maps = {
        "USER": user_map,
        "TEST_CASE": case_map,
        "TEST_CASE_VERSION": version_map,
        "REVIEW_REQUEST": review_map,
        "TEST_SUITE": suite_map,
        "TEST_SUITE_RUN": suite_run_map,
        "RUN_JOB": job_map,
        "RUN_RESULT": result_map,
        "ARTIFACT": artifact_map,
    }
    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.audit_logs ORDER BY id"):
        entity_map = entity_maps.get(row["entity_type"], {})
        bind.execute(
            tables["audit_logs"].insert().values(
                project_id=project_id,
                actor_user_id=user_map.get(row["actor_user_id"]),
                action=row["action"],
                entity_type=row["entity_type"],
                entity_id=entity_map.get(row["entity_id"]),
                entity_version_id=version_map.get(row["entity_version_id"]),
                request_id=str(row["request_id"]) if row["request_id"] else None,
                ip=row["ip"],
                user_agent=row["user_agent"],
                before_data=row["before_data"],
                after_data=row["after_data"],
                created_at=row["created_at"],
            )
        )

    for row in _rows(bind, f"SELECT * FROM {LEGACY_SCHEMA}.test_case_search_documents"):
        document_id = insert_id(
            "test_case_search_documents",
            {
                "project_id": project_id,
                "version_id": version_map[row["version_id"]],
                "search_text": row["search_text"],
                "embedding_model": row["embedding_model"],
                "embedding_status": str(row["embedding_status"]),
                "content_hash": row["content_hash"],
                "indexed_at": row["indexed_at"],
                "last_error": row["last_error"],
            },
        )
        if row.get("embedding") is not None:
            bind.execute(
                sa.text(
                    "UPDATE test_case_search_documents SET embedding = CAST(:embedding AS vector) WHERE id = :id"
                ),
                {"embedding": str(row["embedding"]), "id": document_id},
            )

    op.execute(
        """
        CREATE INDEX idx_tc_search_embedding_hnsw
        ON test_case_search_documents USING hnsw (embedding vector_cosine_ops)
        WHERE embedding_status = 'READY'
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_approved_suite_item()
        RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM test_case_versions
            WHERE id = NEW.test_case_version_id
              AND project_id = NEW.project_id
              AND status = 'APPROVED'
          ) THEN
            RAISE EXCEPTION 'Only APPROVED test case versions in the same project can be added to a suite';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_suite_item_approved
        BEFORE INSERT OR UPDATE ON test_suite_items
        FOR EACH ROW EXECUTE FUNCTION enforce_approved_suite_item()
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0004 keeps the UUID schema in legacy_uuid_0004; restore it explicitly after backing up the BIGINT schema"
    )
