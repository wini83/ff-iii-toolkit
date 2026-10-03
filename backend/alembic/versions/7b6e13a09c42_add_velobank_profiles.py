"""add user-owned VeloBank account mappings"""

import sqlalchemy as sa

from alembic import op
from src.services.db.types import GUID

revision = "7b6e13a09c42"
down_revision = "4d7f8e9a1b2c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "velobank_profiles",
        sa.Column(
            "user_id",
            GUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("accounts_json", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("velobank_profiles")
