"""initial schema: books, parts, chapters, sections"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the four core tables: books, parts, chapters, sections.

    Intent: establish the persistent structure mirroring the Pydantic domain models,
    with `ON DELETE CASCADE` everywhere so removing a Book cleans up the whole tree.
    """
    op.create_table(
        "books",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("author", sa.String(), nullable=True),
        sa.Column("file_path", sa.String(), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "parts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column(
            "parent_book_id",
            sa.String(),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_parts_parent_book_id", "parts", ["parent_book_id"])

    op.create_table(
        "chapters",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("spine_file_path", sa.String(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column(
            "parent_book_id",
            sa.String(),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_part_id",
            sa.String(),
            sa.ForeignKey("parts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_chapters_parent_book_id", "chapters", ["parent_book_id"])
    op.create_index("ix_chapters_parent_part_id", "chapters", ["parent_part_id"])

    op.create_table(
        "sections",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("spine_file_path", sa.String(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column(
            "parent_chapter_id",
            sa.String(),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_sections_parent_chapter_id", "sections", ["parent_chapter_id"])


def downgrade() -> None:
    """Drop the four tables in reverse dependency order.

    Intent: reverse of `upgrade` so `alembic downgrade base` returns to a clean schema.
    """
    op.drop_table("sections")
    op.drop_table("chapters")
    op.drop_table("parts")
    op.drop_table("books")
