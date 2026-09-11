"""Add sent content tracking columns to campaign_recipients.

Run from the server directory:
    python3 scripts/add_sent_content_tracking.py --env-file env/.env.production
    python3 scripts/add_sent_content_tracking.py

This migration adds optional columns to track:
- sent_subject: The subject line that was actually sent
- sent_body_html: The HTML body that was actually sent (rendered with variables)
- sent_from_email: The from email address used
- sent_from_name: The from name used

These columns are nullable and only populated for newly sent emails.
Existing sent emails will have NULL values until re-sent or backfilled.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from sqlalchemy import text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Add sent content tracking to campaign_recipients")
    parser.add_argument("--env-file", type=str, default=None, help="Path to env file")
    args = parser.parse_args()

    if args.env_file:
        import os
        os.environ["APP_ENV_FILE"] = args.env_file

    from database import engine

    # Check if columns already exist
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'campaign_recipients'
                AND column_name IN ('sent_subject', 'sent_body_html', 'sent_from_email', 'sent_from_name')
            """)
        )
        existing_columns = {row[0] for row in result}

    # Add columns that don't exist (each in its own transaction)
    columns_to_add = {
        'sent_subject': 'VARCHAR(500)',
        'sent_body_html': 'TEXT',
        'sent_from_email': 'VARCHAR(255)',
        'sent_from_name': 'VARCHAR(255)',
    }
    
    for column_name, column_type in columns_to_add.items():
        if column_name not in existing_columns:
            alter_sql = f"ALTER TABLE campaign_recipients ADD COLUMN {column_name} {column_type} DEFAULT NULL"
            print(f"Adding column: {column_name}")
            with engine.begin() as conn:
                conn.execute(text(alter_sql))
        else:
            print(f"Column {column_name} already exists — skipping.")
    
    print("✓ Sent content tracking columns added successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
