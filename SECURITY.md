# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately to the repository owner instead of opening a public issue. Include:

- the affected endpoint or component;
- steps to reproduce;
- expected and observed behavior;
- potential impact; and
- a minimal proof of concept with production data removed.

Do not access another user's data, send email, alter queues, or test against production accounts without written authorization.

## Security expectations

- All user-owned database queries must enforce tenant ownership.
- Secrets belong in environment variables and must never be committed.
- Tracking links and provider webhooks must be authenticated or cryptographically verified.
- Logs and API responses must redact credentials and tokens.
- Destructive operations must validate ownership and dependency impact.
- Bulk imports must remain staged until explicit approval.

## Excluded data

The repository intentionally excludes production environment files, database dumps, Redis snapshots, recipient exports, uploaded attachments, logs, local databases, and virtual environments. If any credential is committed accidentally, revoke it before rewriting repository history.
