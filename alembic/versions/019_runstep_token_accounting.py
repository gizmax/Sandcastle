"""Record token usage and compaction savings per run step.

Until now a step recorded only ``cost_usd``, so anything needing token counts
had to derive them backwards from cost through a blended price - which is what
the evolution engine does today. These columns let the engine record what the
provider actually reported, and how many tokens context compaction removed
before the step ran.

Revision ID: 019
Revises: 018
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # input/output tokens stay nullable: not every provider reports usage, and
    # NULL ("unknown") must stay distinguishable from 0 ("reported as none").
    op.add_column("run_steps", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("run_steps", sa.Column("output_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "run_steps",
        sa.Column("tokens_saved", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_steps", sa.Column("compaction_strategy", sa.String(length=32), nullable=True)
    )
    op.create_check_constraint(
        "ck_run_steps_tokens_saved_non_negative", "run_steps", "tokens_saved >= 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_run_steps_tokens_saved_non_negative", "run_steps", type_="check")
    op.drop_column("run_steps", "compaction_strategy")
    op.drop_column("run_steps", "tokens_saved")
    op.drop_column("run_steps", "output_tokens")
    op.drop_column("run_steps", "input_tokens")
