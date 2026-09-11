#!/usr/bin/env python3
"""
Email Worker - Background Process

Run this as a separate process to handle email sending:
    python worker.py

Or with specific settings:
    python worker.py --workers 5 --batch-size 20

The worker will:
1. Process the Redis email queue
2. Send emails via SMTP
3. Update campaign progress
4. Handle retries automatically
"""

import argparse
import sys
import os
from env_loader import load_app_env

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


def main():
    parser = argparse.ArgumentParser(description='Email Worker Process')
    parser.add_argument(
        '--env-file',
        type=str,
        default=None,
        help='Path to env file (e.g. .env.production). Falls back to auto-detection when omitted.'
    )
    parser.add_argument(
        '--workers', '-w',
        type=int,
        default=3,  # Safe now: recipient claiming is atomic (FOR UPDATE SKIP LOCKED)
        help='Number of worker threads (default: 3)'
    )
    parser.add_argument(
        '--batch-size', '-b',
        type=int,
        default=10,
        help='Emails to process per batch (default: 10)'
    )
    parser.add_argument(
        '--poll-interval', '-p',
        type=float,
        default=1.0,
        help='Seconds between queue polls (default: 1.0)'
    )
    parser.add_argument(
        '--timezone', '-tz',
        type=str,
        default=None,
        help='Worker timezone (e.g. US/Eastern, US/Central). Overrides WORKER_TIMEZONE env var.'
    )
    parser.add_argument(
        '--name',
        type=str,
        default=None,
        help='Instance name (e.g. us_eastern_2) for multiple workers on the same timezone. Overrides WORKER_NAME env var.'
    )
    
    args = parser.parse_args()

    # Resolve environment before importing worker modules that read env vars.
    if args.env_file:
        os.environ["APP_ENV_FILE"] = args.env_file
    env_file = load_app_env()

    from services.worker_service import run_worker, worker_service
    
    # --timezone flag overrides env var
    if args.timezone:
        os.environ["WORKER_TIMEZONE"] = args.timezone

    # --name flag overrides env var
    if args.name:
        os.environ["WORKER_NAME"] = args.name

    # Production safety: require timezone-scoped workers by default.
    # This avoids accidental generic workers that can race with ET/PT services.
    env_name = (os.environ.get("ENVIRONMENT") or os.environ.get("ENV") or "").strip().lower()
    worker_tz = (os.environ.get("WORKER_TIMEZONE") or "").strip()
    allow_unscoped = (os.environ.get("ALLOW_UNSCOPED_WORKER", "false").strip().lower() in {"1", "true", "yes", "on"})
    if env_name == "production" and not worker_tz and not allow_unscoped:
        logging.error(
            "Refusing to start unscoped worker in production. "
            "Set WORKER_TIMEZONE (or --timezone), or set ALLOW_UNSCOPED_WORKER=true to override."
        )
        sys.exit(2)
    
    tz_display = os.environ.get("WORKER_TIMEZONE", "any (no filter)")
    
    print(f"""
╔══════════════════════════════════════════════════════╗
║          FLEETCTRL-X - Worker Process                ║
╠══════════════════════════════════════════════════════╣
║  Workers:       {args.workers:<5}                              ║
║  Batch Size:    {args.batch_size:<5}                              ║
║  Poll Interval: {args.poll_interval:<5}s                             ║
║  Timezone:      {tz_display:<30}       ║
║  Env File:      {(env_file or 'auto/default')[:30]:<30}       ║
║  Biz Hours:     9AM-5PM Mon-Fri                      ║
╚══════════════════════════════════════════════════════╝
    """)
    
    # Configure and run worker
    worker_service.num_workers = args.workers
    worker_service.batch_size = args.batch_size
    worker_service.poll_interval = args.poll_interval
    
    run_worker()


if __name__ == "__main__":
    main()
