"""Migrate operational sender-domain data in PostgreSQL and Redis.

The command is a dry run unless ``--apply`` is supplied. Stop API/worker
services before applying so no email is queued or sent during the cutover.

Examples (run from the server directory):
    python3 scripts/migrate_sending_domain.py --env-file env/.env.production
    python3 scripts/migrate_sending_domain.py --env-file env/.env.production --apply
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Optional


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


OLD_DOMAIN = "engieservices.net"
NEW_DOMAIN = "emailsuport.com"

# Only operational addresses are changed. Historical delivery records and
# message IDs remain untouched so the audit trail continues to match messages
# that were actually sent from the old domain.
DATABASE_TARGETS = (
    ("smtp_accounts", "from_email", None),
    ("smtp_accounts", "imap_username", None),
    ("inboxes", "email", None),
    ("inboxes", "imap_username", None),
    ("inboxes", "reply_to_email", None),
    ("campaigns", "reply_to_email", None),
    ("fallback_reply_emails", "email", None),
    ("warmup_replies", "to_email", "status::text IN ('pending', 'queued')"),
    ("warmup_replies", "from_email", "status::text IN ('pending', 'queued')"),
    ("campaign_auto_replies", "to_email", "status IN ('pending', 'queued')"),
    ("campaign_auto_replies", "from_email", "status IN ('pending', 'queued')"),
    ("emails", "from_email", "status::text IN ('queued', 'sending')"),
    ("emails", "to_email", "status::text IN ('queued', 'sending')"),
)

QUEUE_KEYS = (
    "email:queue:high",
    "email:queue:normal",
    "email:queue:low",
)
PROCESSING_KEY = "email:processing"
ACTIVE_IDS_KEY = "email:active_ids"
QUEUE_ADDRESS_FIELDS = (
    "from_email",
    "to_email",
    "reply_to",
    "reply_to_email",
    "sender_email",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-domain", default=OLD_DOMAIN)
    parser.add_argument("--new-domain", default=NEW_DOMAIN)
    parser.add_argument(
        "--env-file",
        help="Environment file relative to the server directory or an absolute path",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit database and Redis changes (default: report only)",
    )
    return parser.parse_args()


def configure_environment(env_file: Optional[str]) -> None:
    if not env_file:
        return

    path = Path(env_file)
    if not path.is_absolute():
        path = SERVER_ROOT / path
    os.environ["APP_ENV_FILE"] = str(path.resolve())


def validate_domain(domain: str) -> str:
    value = domain.strip().lower().rstrip(".")
    if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value):
        raise ValueError(f"Invalid domain: {domain!r}")
    return value


def domain_pattern(domain: str) -> str:
    return re.escape(domain)


def check_inbox_collisions(connection, old_domain: str, new_domain: str) -> None:
    from sqlalchemy import text

    rows = connection.execute(
        text(
            """
            SELECT old_inbox.id AS old_id, new_inbox.id AS new_id,
                   old_inbox.email AS old_email, new_inbox.email AS new_email
            FROM inboxes AS old_inbox
            JOIN inboxes AS new_inbox
              ON old_inbox.user_id IS NOT DISTINCT FROM new_inbox.user_id
             AND lower(split_part(old_inbox.email, '@', 1)) = lower(split_part(new_inbox.email, '@', 1))
            WHERE lower(split_part(old_inbox.email, '@', 2)) = :old_domain
              AND lower(split_part(new_inbox.email, '@', 2)) = :new_domain
            ORDER BY old_inbox.id
            """
        ),
        {"old_domain": old_domain, "new_domain": new_domain},
    ).all()
    if rows:
        details = ", ".join(
            f"#{row.old_id} {row.old_email} conflicts with #{row.new_id} {row.new_email}"
            for row in rows
        )
        raise RuntimeError(f"Inbox address collision(s): {details}")


def migrate_database(engine, old_domain: str, new_domain: str, apply: bool) -> int:
    from sqlalchemy import text

    pattern = domain_pattern(old_domain)
    total = 0
    with engine.begin() as connection:
        check_inbox_collisions(connection, old_domain, new_domain)

        for table, column, extra_condition in DATABASE_TARGETS:
            condition = f"{column} ~* :pattern"
            if extra_condition:
                condition += f" AND {extra_condition}"

            count = connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE {condition}"),
                {"pattern": pattern},
            ).scalar_one()
            total += count
            print(f"database|{table}.{column}|matches={count}")

            if apply and count:
                connection.execute(
                    text(
                        f"UPDATE {table} "
                        f"SET {column} = regexp_replace({column}, :pattern, :new_domain, 'gi') "
                        f"WHERE {condition}"
                    ),
                    {"pattern": pattern, "new_domain": new_domain},
                )

        if not apply:
            connection.rollback()

    return total


def replace_queue_member(member: str, pattern: re.Pattern[str], new_domain: str) -> tuple[str, Optional[str], Optional[str]]:
    try:
        payload = json.loads(member)
    except (json.JSONDecodeError, TypeError):
        return member, None, None

    if not isinstance(payload, dict):
        return member, None, None

    changed = False
    for field in QUEUE_ADDRESS_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and pattern.search(value):
            payload[field] = pattern.sub(new_domain, value)
            changed = True

    if not changed:
        return member, payload.get("_id"), payload.get("_id")

    old_id = payload.get("_id")
    new_id = old_id
    if isinstance(old_id, str) and pattern.search(old_id):
        new_id = pattern.sub(new_domain, old_id)
        payload["_id"] = new_id

    return json.dumps(payload), old_id, new_id


def migrate_redis(redis_url: str, old_domain: str, new_domain: str, apply: bool) -> int:
    import redis

    client = redis.from_url(redis_url, decode_responses=True)
    processing_count = client.hlen(PROCESSING_KEY)
    if apply and processing_count:
        raise RuntimeError(
            f"Refusing to migrate with {processing_count} email(s) in {PROCESSING_KEY}; "
            "stop workers and wait for processing to reach zero"
        )

    pattern = re.compile(re.escape(old_domain), re.IGNORECASE)
    total = 0
    changes: list[tuple[str, str, str, float, Optional[str], Optional[str]]] = []

    for key in QUEUE_KEYS:
        key_matches = 0
        for member, score in client.zrange(key, 0, -1, withscores=True):
            replaced, old_id, new_id = replace_queue_member(member, pattern, new_domain)
            if replaced == member:
                continue
            changes.append((key, member, replaced, score, old_id, new_id))
            key_matches += 1
        total += key_matches
        print(f"redis|{key}|matches={key_matches}")

    print(f"redis|{PROCESSING_KEY}|active={processing_count}")

    if apply and changes:
        pipeline = client.pipeline(transaction=True)
        for key, old_member, new_member, score, old_id, new_id in changes:
            pipeline.zrem(key, old_member)
            pipeline.zadd(key, {new_member: score})
            if old_id and new_id and old_id != new_id:
                pipeline.srem(ACTIVE_IDS_KEY, old_id)
                pipeline.sadd(ACTIVE_IDS_KEY, new_id)
        pipeline.execute()

    return total


def main() -> None:
    args = parse_args()
    old_domain = validate_domain(args.old_domain)
    new_domain = validate_domain(args.new_domain)
    if old_domain == new_domain:
        raise ValueError("Old and new domains must differ")

    configure_environment(args.env_file)

    from env_loader import load_app_env

    loaded_env = load_app_env()
    from database import engine

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"mode={mode}|old={old_domain}|new={new_domain}|env={loaded_env or 'process environment'}")

    database_matches = migrate_database(engine, old_domain, new_domain, args.apply)
    redis_matches = migrate_redis(redis_url, old_domain, new_domain, args.apply)

    print(f"summary|database_matches={database_matches}|queue_matches={redis_matches}")
    if not args.apply:
        print("No changes made. Re-run with --apply after provider, DNS, and mailbox verification.")
    else:
        print("Migration applied. Restart services and run the post-cutover checks.")


if __name__ == "__main__":
    main()