"""0017 — schedule_file dedup becomes per scope, not global.

``store_file`` has always deduplicated within a scope, and says why in its docstring: an
integrated master schedule legitimately belongs to more than one project, and a global
hash match would hand the second project a file owned by the first. The schema never
agreed. ``ix_schedule_file_content_sha256`` was created ``unique=True`` in 0009, before
scopes existed, and 0014 added ``scope_id`` without widening it.

Nothing hit the wall because nothing was reaching the second scope: the upload route read
``scope_id`` from the form body while the client sent it in the query string, so every
file landed on the default project and the dedup branch always fired before the index
could refuse. Fixing that surfaced this immediately as a 500.

This is the same edit 0014 made to ``risk``: drop the global unique index, keep a plain
one for lookup, and put uniqueness on the pair.

Downgrade re-narrows the index and will fail on any database where the same export was
uploaded to two scopes — correctly, since that is data this constraint cannot hold.

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

TABLE = "schedule_file"
INDEX = "ix_schedule_file_content_sha256"
CONSTRAINT = "uq_schedule_file_scope_sha256"


def upgrade() -> None:
    # The hash stays indexed — "where else did these bytes land" is a question worth
    # answering cheaply — it just stops being unique on its own.
    op.drop_index(INDEX, table_name=TABLE)
    op.create_index(INDEX, TABLE, ["content_sha256"])
    with op.batch_alter_table(TABLE) as batch:
        batch.create_unique_constraint(CONSTRAINT, ["scope_id", "content_sha256"])


def downgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(CONSTRAINT, type_="unique")
    op.drop_index(INDEX, table_name=TABLE)
    op.create_index(INDEX, TABLE, ["content_sha256"], unique=True)
