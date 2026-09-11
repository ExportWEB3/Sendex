# Sendex Server

FastAPI application and queue workers for multi-tenant campaign orchestration, message delivery, sender warm-up, replies, webhook processing, and operational monitoring.

## Responsibilities

- Authenticated, tenant-isolated REST APIs
- Campaign planning and recipient state transitions
- Redis-backed priority queues and worker leases
- Resend and SMTP delivery
- IMAP reply ingestion and automatic reply workflows
- Domain pacing, quotas, bounce limits, and complaint controls
- Signed tracking, unsubscribe, and provider webhooks
- Non-mutating AI-assisted bundle analysis and approved materialization
- Health checks, worker heartbeats, logs, and Telegram alerts

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp env/.env.example env/.env
```

Create the PostgreSQL database, start Redis, and replace the local placeholders in `env/.env`. Then run:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8090
```

Start a development worker separately:

```bash
python worker.py
```

Keep production API and worker processes under a supervisor such as systemd. Each deployed worker should have an explicit name and timezone scope.

## Quality checks

```bash
TEST_REDIS_URL=redis://localhost:6379/15 pytest
ruff check .
```

Tests must use isolated database and Redis resources. Never point automated tests at production.

## Structure

- `api/`: HTTP routes and authorization boundaries
- `models/`: SQLAlchemy persistence models
- `schemas/`: request and response contracts
- `services/`: campaign, queue, delivery, health, assistant, and monitoring logic
- `scripts/`: explicit maintenance and migration utilities
- `tests/`: unit and regression coverage
- `main.py`: application composition and lifecycle
- `worker.py`: queue worker entry point

## Data and secret safety

Only the sanitized `env/.env.example` is publishable. Local environment files, uploads, logs, test artifacts, database files, recipient exports, and backups are intentionally ignored.
