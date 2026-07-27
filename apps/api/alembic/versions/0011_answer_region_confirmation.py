"""add answer region confirmation state

Revision ID: 0011_answer_region_confirmation
Revises: 0010_report_student
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0011_answer_region_confirmation"
down_revision: str | None = "0010_report_student"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "student_answer_regions"


def _add_offline_schema() -> None:
    """Emit deterministic SQL for a clean, sequential upgrade from 0010."""
    op.add_column(
        TABLE,
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
    )
    op.add_column(
        TABLE,
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_student_answer_regions_status", TABLE, ["status"], unique=False)
    op.create_foreign_key(
        "fk_student_answer_regions_confirmed_by_users",
        TABLE,
        "users",
        ["confirmed_by"],
        ["id"],
        ondelete="SET NULL",
    )


def _drop_offline_schema() -> None:
    """Emit dependency-ordered SQL for a complete sequential downgrade."""
    op.drop_constraint(
        "fk_student_answer_regions_confirmed_by_users",
        TABLE,
        type_="foreignkey",
    )
    op.drop_index("ix_student_answer_regions_status", table_name=TABLE)
    for column in ("confirmed_at", "confirmed_by", "status"):
        op.drop_column(TABLE, column)


def _column(name: str) -> Any:
    return next(
        (
            column
            for column in sa.inspect(op.get_bind()).get_columns(TABLE)
            if column["name"] == name
        ),
        None,
    )


def _ensure_column(column: sa.Column[Any]) -> None:
    existing = _column(column.name)
    if existing is None:
        op.add_column(TABLE, column)
        return
    actual_type = existing["type"]
    expected_type = column.type
    same_type = actual_type._type_affinity is expected_type._type_affinity
    same_shape = all(
        getattr(actual_type, attribute, None) == getattr(expected_type, attribute, None)
        for attribute in ("length", "precision", "scale")
    )
    if not same_type or not same_shape or existing["nullable"] != column.nullable:
        raise RuntimeError(f"incompatible existing column: {TABLE}.{column.name}")


def _ensure_index(name: str, columns: list[str]) -> None:
    indexes = sa.inspect(op.get_bind()).get_indexes(TABLE)
    existing = next((index for index in indexes if index["name"] == name), None)
    if existing is None:
        op.create_index(name, TABLE, columns, unique=False)
    elif existing["column_names"] != columns or existing["unique"]:
        raise RuntimeError(f"incompatible existing index: {name}")


def _ensure_foreign_key() -> None:
    name = "fk_student_answer_regions_confirmed_by_users"
    foreign_keys = sa.inspect(op.get_bind()).get_foreign_keys(TABLE)

    def compatible(constraint: Any) -> bool:
        options = constraint.get("options") or {}
        return (
            constraint["constrained_columns"] == ["confirmed_by"]
            and constraint["referred_table"] == "users"
            and constraint["referred_columns"] == ["id"]
            and str(options.get("ondelete", "")).upper() == "SET NULL"
        )

    named = next((constraint for constraint in foreign_keys if constraint["name"] == name), None)
    if named is not None:
        if not compatible(named):
            raise RuntimeError(f"incompatible existing foreign key: {name}")
        return
    equivalents = [constraint for constraint in foreign_keys if compatible(constraint)]
    conflicting = [
        constraint
        for constraint in foreign_keys
        if constraint["constrained_columns"] == ["confirmed_by"] and not compatible(constraint)
    ]
    if conflicting or len(equivalents) > 1:
        raise RuntimeError(f"ambiguous existing foreign key for {TABLE}.confirmed_by")
    if equivalents:
        old_name = equivalents[0]["name"]
        if not isinstance(old_name, str):
            raise RuntimeError(f"unnamed existing foreign key for {TABLE}.confirmed_by")
        op.execute(sa.text(f'ALTER TABLE "{TABLE}" RENAME CONSTRAINT "{old_name}" TO "{name}"'))
        return
    op.create_foreign_key(
        name,
        TABLE,
        "users",
        ["confirmed_by"],
        ["id"],
        ondelete="SET NULL",
    )


def upgrade() -> None:
    # Offline SQL targets the known schema produced by revision 0010. Partial-schema
    # idempotency and conflict detection remain intentionally limited to online mode.
    if context.is_offline_mode():
        _add_offline_schema()
        return
    _ensure_column(
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending")
    )
    _ensure_column(sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True))
    _ensure_column(sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column(TABLE, "status", server_default="pending")
    _ensure_index("ix_student_answer_regions_status", ["status"])
    _ensure_foreign_key()


def downgrade() -> None:
    if context.is_offline_mode():
        _drop_offline_schema()
        return
    foreign_keys = sa.inspect(op.get_bind()).get_foreign_keys(TABLE)
    if any(
        constraint["name"] == "fk_student_answer_regions_confirmed_by_users"
        for constraint in foreign_keys
    ):
        op.drop_constraint(
            "fk_student_answer_regions_confirmed_by_users",
            TABLE,
            type_="foreignkey",
        )
    if any(
        index["name"] == "ix_student_answer_regions_status"
        for index in sa.inspect(op.get_bind()).get_indexes(TABLE)
    ):
        op.drop_index(
            op.f("ix_student_answer_regions_status"),
            table_name=TABLE,
        )
    for column in ("confirmed_at", "confirmed_by", "status"):
        if _column(column) is not None:
            op.drop_column(TABLE, column)
