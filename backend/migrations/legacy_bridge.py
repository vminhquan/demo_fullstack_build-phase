"""Move databases created by the former 0001-0007 migration chain onto the 0001_baseline revision.

Those revision files now live in migrations/legacy/ and are no longer loaded, so Alembic would fail
with "Can't locate revision" on such a database. Before migrations run, env.py calls
`bridge_legacy_revision`, which (in the same transaction):

* 0007_roles_responsibilities -> schema already equals the baseline; only the revision is rewritten.
* 0006_suite_item_trigger     -> applies the former 0007 change (roles/responsibilities), frozen as
                                 plain SQL below, then rewrites the revision.
* 0001-0005                   -> refused; upgrade with a release that still ships the old chain first.

All SQL here is literal so the result never depends on the current app models.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

BASELINE_REVISION = "0001_baseline"
LEGACY_HEAD = "0007_roles_responsibilities"
LEGACY_BEFORE_ROLES = "0006_suite_item_trigger"
LEGACY_UNSUPPORTED = {
    "0001_initial_flow_b",
    "0002_rename_author_to_creator",
    "0003_add_edit_review_status",
    "0004_project_scope_bigint",
    "0005_rebuild_project_audit_logs",
}

_ROLES_AND_RESPONSIBILITIES = """
CREATE TABLE roles (
    code VARCHAR(32) NOT NULL,
    name VARCHAR(80) NOT NULL,
    description TEXT,
    PRIMARY KEY (code)
);
INSERT INTO roles (code, name, description) VALUES
    ('ADMIN', 'Quản trị viên', 'Quản lý thành viên, nhiệm vụ và Project'),
    ('MEMBER', 'Thành viên', 'Thành viên của Project');

CREATE TABLE responsibilities (
    code VARCHAR(32) NOT NULL,
    name VARCHAR(80) NOT NULL,
    description TEXT,
    requires_code VARCHAR(32),
    PRIMARY KEY (code),
    FOREIGN KEY (requires_code) REFERENCES responsibilities (code)
);
INSERT INTO responsibilities (code, name, description, requires_code) VALUES
    ('TESTCASE_CREATE', 'Tạo test case', NULL, NULL),
    ('TESTCASE_REVIEW', 'Duyệt test case', NULL, NULL),
    ('TESTCASE_SELF_REVIEW', 'Tự duyệt test case của mình', NULL, 'TESTCASE_REVIEW');

CREATE TABLE project_user_responsibilities (
    project_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    responsibility_code VARCHAR(32) NOT NULL,
    assigned_by BIGINT NOT NULL,
    assigned_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    PRIMARY KEY (project_id, user_id, responsibility_code),
    FOREIGN KEY (project_id, user_id) REFERENCES project_users (project_id, user_id) ON DELETE CASCADE,
    FOREIGN KEY (responsibility_code) REFERENCES responsibilities (code),
    FOREIGN KEY (assigned_by) REFERENCES users (id) ON DELETE RESTRICT
);

-- Old role -> new role + responsibilities:
--   project creator -> ADMIN + every responsibility
--   ADMIN -> ADMIN, CREATOR -> MEMBER + TESTCASE_CREATE, REVIEWER -> MEMBER + TESTCASE_REVIEW
--   is_active = false -> membership removed
DELETE FROM project_users WHERE is_active = false;
ALTER TABLE project_users ADD COLUMN role_code VARCHAR(32);
UPDATE project_users SET role_code = CASE WHEN role::text = 'ADMIN' THEN 'ADMIN' ELSE 'MEMBER' END;
INSERT INTO project_user_responsibilities (project_id, user_id, responsibility_code, assigned_by)
SELECT project_id, user_id,
       CASE role::text WHEN 'CREATOR' THEN 'TESTCASE_CREATE' ELSE 'TESTCASE_REVIEW' END,
       added_by
FROM project_users
WHERE role::text IN ('CREATOR', 'REVIEWER');
DROP INDEX IF EXISTS idx_project_users_user_active;
ALTER TABLE project_users DROP COLUMN role, DROP COLUMN is_active;
-- The archived legacy_uuid_0004 schema may still use the enum; keep it in that case.
DO $$
BEGIN
  DROP TYPE IF EXISTS role_code;
EXCEPTION WHEN dependent_objects_still_exist THEN
  NULL;
END $$;
ALTER TABLE project_users ALTER COLUMN role_code SET NOT NULL;
ALTER TABLE project_users
    ADD CONSTRAINT project_users_role_code_fkey FOREIGN KEY (role_code) REFERENCES roles (code);
CREATE INDEX ix_project_users_role_code ON project_users (role_code);
CREATE INDEX idx_project_users_user ON project_users (user_id);

INSERT INTO project_users (project_id, user_id, role_code, added_by)
SELECT id, created_by, 'ADMIN', created_by FROM projects
ON CONFLICT (project_id, user_id) DO UPDATE SET role_code = 'ADMIN';
INSERT INTO project_user_responsibilities (project_id, user_id, responsibility_code, assigned_by)
SELECT p.id, p.created_by, r.code, p.created_by
FROM projects p CROSS JOIN responsibilities r
ON CONFLICT DO NOTHING;

ALTER TABLE projects
    ADD COLUMN deleted_at TIMESTAMPTZ,
    ADD COLUMN deleted_by BIGINT REFERENCES users (id) ON DELETE RESTRICT;
CREATE INDEX ix_projects_deleted_at ON projects (deleted_at);
"""


def _current_revision(connection: Connection) -> str | None:
    exists = connection.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL")).scalar_one()
    if not exists:
        return None
    return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()


def bridge_legacy_revision(connection: Connection) -> None:
    revision = _current_revision(connection)
    if revision in LEGACY_UNSUPPORTED:
        raise RuntimeError(
            f"Database is at legacy revision {revision}. Upgrade it to 0006_suite_item_trigger with a "
            "release that still contains the former migration chain, then run this migration again."
        )
    if revision == LEGACY_BEFORE_ROLES:
        connection.exec_driver_sql(_ROLES_AND_RESPONSIBILITIES)
        revision = LEGACY_HEAD
    if revision == LEGACY_HEAD:
        connection.execute(
            text("UPDATE alembic_version SET version_num = :baseline"), {"baseline": BASELINE_REVISION}
        )
