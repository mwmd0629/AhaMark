"""classes, students, groups and two-phase imports"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_classes_students_imports"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    archive = sa.Enum("active", "archived", name="archivestatus")
    membership = sa.Enum("active", "removed", name="membershipstatus")
    import_status = sa.Enum(
        "preview_ready", "validation_failed", "confirmed", "failed", "expired", name="importstatus"
    )
    row_status = sa.Enum(
        "valid",
        "invalid",
        "duplicate_in_file",
        "duplicate_existing",
        "confirmed",
        "skipped",
        name="importrowstatus",
    )
    uuid = postgresql.UUID(as_uuid=True)

    def timestamps() -> list[sa.Column[object]]:
        return [
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        ]

    op.create_table(
        "classes",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("grade", sa.String(40)),
        sa.Column("subject", sa.String(40)),
        sa.Column("academic_year", sa.String(20)),
        sa.Column("semester", sa.String(30)),
        sa.Column("description", sa.Text()),
        sa.Column("status", archive, nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("owner_id", "name", name="uq_class_owner_name"),
    )
    for col in ["owner_id", "grade", "subject", "status"]:
        op.create_index(f"ix_classes_{col}", "classes", [col])
    op.create_table(
        "students",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("student_number", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("gender", sa.String(20)),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(40)),
        sa.Column("status", archive, nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("owner_id", "student_number", name="uq_student_owner_number"),
    )
    for col in ["owner_id", "student_number", "name", "status"]:
        op.create_index(f"ix_students_{col}", "students", [col])
    op.create_table(
        "class_students",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "class_id", uuid, sa.ForeignKey("classes.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "student_id", uuid, sa.ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", membership, nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("class_id", "student_id", name="uq_class_student"),
    )
    for col in ["class_id", "student_id", "status"]:
        op.create_index(f"ix_class_students_{col}", "class_students", [col])
    op.create_table(
        "student_groups",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "class_id", uuid, sa.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.String(255)),
        *timestamps(),
        sa.UniqueConstraint("class_id", "name", name="uq_group_class_name"),
    )
    op.create_index("ix_student_groups_class_id", "student_groups", ["class_id"])
    op.create_table(
        "student_group_members",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "group_id", uuid, sa.ForeignKey("student_groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "student_id", uuid, sa.ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", "student_id", name="uq_group_student"),
    )
    for col in ["group_id", "student_id"]:
        op.create_index(f"ix_student_group_members_{col}", "student_group_members", [col])
    op.create_table(
        "import_jobs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "class_id", uuid, sa.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("file_type", sa.String(10), nullable=False),
        sa.Column("status", import_status, nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("valid_rows", sa.Integer(), nullable=False),
        sa.Column("invalid_rows", sa.Integer(), nullable=False),
        sa.Column("duplicate_rows", sa.Integer(), nullable=False),
        sa.Column("confirmed_rows", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ["owner_id", "class_id", "status", "expires_at"]:
        op.create_index(f"ix_import_jobs_{col}", "import_jobs", [col])
    op.create_table(
        "import_rows",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "import_job_id",
            uuid,
            sa.ForeignKey("import_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column("normalized_data", sa.JSON(), nullable=False),
        sa.Column("status", row_status, nullable=False),
        sa.Column("student_id", uuid, sa.ForeignKey("students.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("import_job_id", "row_number", name="uq_import_row"),
    )
    for col in ["import_job_id", "status"]:
        op.create_index(f"ix_import_rows_{col}", "import_rows", [col])
    op.create_table(
        "import_errors",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "import_job_id",
            uuid,
            sa.ForeignKey("import_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("message", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ["import_job_id", "code"]:
        op.create_index(f"ix_import_errors_{col}", "import_errors", [col])


def downgrade() -> None:
    for table in [
        "import_errors",
        "import_rows",
        "import_jobs",
        "student_group_members",
        "student_groups",
        "class_students",
        "students",
        "classes",
    ]:
        op.drop_table(table)
    for name in ["importrowstatus", "importstatus", "membershipstatus", "archivestatus"]:
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
