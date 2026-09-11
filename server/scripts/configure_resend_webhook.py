"""Create the application Resend webhook and store its signing secret safely.

Run from the server directory:
    python3 scripts/configure_resend_webhook.py --env-file env/.env.production
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import dotenv_values
import resend


SERVER_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENDPOINT = "https://metismachine.com/api/resend/webhook"
MANAGED_ENDPOINTS = {
    DEFAULT_ENDPOINT,
    "https://mail.engieservices.net/api/resend/webhook",
}
EVENTS = [
    "email.delivered",
    "email.delivery_delayed",
    "email.bounced",
    "email.complained",
    "email.opened",
    "email.clicked",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    return parser.parse_args()


def resolve_env_file(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = SERVER_ROOT / path
    if not path.exists():
        raise FileNotFoundError(path)
    return path.resolve()


def set_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text().splitlines()
    replacement = f"{key}={value}"
    updated = False
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = replacement
            updated = True
            break
    if not updated:
        lines.extend(["", "# Resend webhook verification", replacement])
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    args = parse_args()
    env_file = resolve_env_file(args.env_file)
    config = dotenv_values(env_file)
    api_key = config.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY is missing")

    resend.api_key = api_key
    existing = [
        item for item in resend.Webhooks.list().get("data", [])
        if item.get("endpoint") in MANAGED_ENDPOINTS | {args.endpoint}
    ]
    stored_secret = config.get("RESEND_WEBHOOK_SECRET")

    if existing and stored_secret:
        webhook = existing[0]
        resend.Webhooks.update({
            "webhook_id": webhook["id"],
            "endpoint": args.endpoint,
            "events": EVENTS,
            "status": "enabled",
        })
        print(f"webhook_id={webhook['id']}|status=updated|events={len(EVENTS)}")
        return

    # Resend only returns the signing secret when a webhook is created. If an
    # endpoint exists but its local secret was lost, replace it safely.
    for webhook in existing:
        resend.Webhooks.remove(webhook["id"])

    created = resend.Webhooks.create({
        "endpoint": args.endpoint,
        "events": EVENTS,
    })
    signing_secret = created.get("signing_secret")
    if not signing_secret:
        raise RuntimeError("Resend did not return a webhook signing secret")

    set_env_value(env_file, "RESEND_WEBHOOK_SECRET", signing_secret)
    os.chmod(env_file, 0o600)
    print(f"webhook_id={created['id']}|status=created|events={len(EVENTS)}")


if __name__ == "__main__":
    main()