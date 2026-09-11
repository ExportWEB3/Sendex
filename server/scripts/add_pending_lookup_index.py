"""Create the pending-lookup index on campaign_recipients.

Run from the server directory:
    python3 scripts/add_pending_lookup_index.py --env-file env/.env.production
    python3 scripts/add_pending_lookup_index.py

The index supports the batcher's atomic-claim query pattern:
    WHERE campaign_id = ? AND status = 'PENDING' AND queued_at IS NULL
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from sqlalchemy import text  # noqa: E402


INDEX_NAME = "ix_campaign_recipients_pending_lookup"

CREATE_SQL = (
    "CREATE INDEX IF NOT EXISTS ix_campaign_recipients_pending_lookup "
    "ON campaign_recipients (campaign_id, status, queued_at)"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Add pending-lookup index to campaign_recipients")
    parser.add_argument("--env-file", type=str, default=None, help="Path to env file")
    args = parser.parse_args()

    if args.env_file:
        import os
        os.environ["APP_ENV_FILE"] = args.env_file

    from database import engine

    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = :name"),
            {"name": INDEX_NAME},
        ).first()
        if exists:
            print(f"Index {INDEX_NAME} already exists — nothing to do.")
            return 0

        conn.execute(text(CREATE_SQL))
        print(f"Created index {INDEX_NAME} on campaign_recipients.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
