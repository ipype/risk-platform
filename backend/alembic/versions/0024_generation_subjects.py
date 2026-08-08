"""what a query-shaped generation ran over, and what it declined to ask about

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-08

Two nullable JSON columns on ``generation_run``. Nothing existing is rewritten, no index
is touched, and the downgrade is a clean drop — a 5.4 run reads identically before and
after, with both columns NULL.

**``subject_ids`` is named for subjects and not for risks on purpose.** Qualitative
evaluation is the first query-shaped generator: it runs over rows that already exist rather
than over a corpus. Quantitative elicitation and risk-to-activity mapping are the same
shape and arrive next, and a column called ``risk_ids`` here would be followed by
``estimate_ids`` and ``mapping_ids`` — the point at which one run table starts growing one
column per generator and stops being one table in anything but name.

**``skipped`` is separate from ``dropped`` because they are different findings.** A drop
says the model was asked and its answer was refused; a skip says it was never asked, which
for this generator is the *correct* outcome whenever retrieval found nothing worth citing.
A pass that skipped thirty risks for want of evidence and a pass that asked about thirty
and refused every answer produce the same proposal count and mean opposite things — one is
an empty corpus, the other is a broken prompt. Folding them into one list would let each
hide inside the other exactly when someone needs to tell them apart.

**No CHECK on ``kind``.** There was none in 0023 and adding one now would mean a migration
every time a generator ships, which is the cost 0023's own note declined to take on.
"""

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_run", sa.Column("subject_ids", sa.JSON(), nullable=True))
    op.add_column("generation_run", sa.Column("skipped", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("generation_run", "skipped")
    op.drop_column("generation_run", "subject_ids")
