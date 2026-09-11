import os
from pathlib import Path
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv


@lru_cache(maxsize=1)
def load_app_env() -> str:
    """Load environment variables with production-safe precedence.

    Order:
    1) Explicit `APP_ENV_FILE` / `ENV_FILE` path (if provided)
    2) `.env.production` when running under systemd without DB/Redis env set
    3) `.env.production` when ENV/ENVIRONMENT indicates production
    4) `.env`

    `.env` is loaded as a non-overriding fallback after the selected primary file,
    so missing keys are filled without clobbering explicitly loaded values.

    Returns the primary file that was loaded (or empty string if none found).
    """
    root = Path(__file__).resolve().parent

    explicit = os.getenv("APP_ENV_FILE") or os.getenv("ENV_FILE")
    env_name = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").strip().lower()

    under_systemd = bool(os.getenv("INVOCATION_ID") or os.getenv("JOURNAL_STREAM"))
    has_runtime_db = bool(os.getenv("DATABASE_URL"))
    has_runtime_redis = bool(os.getenv("REDIS_URL"))

    primary: Optional[Path] = None

    if explicit:
        candidate = Path(explicit)
        if not candidate.is_absolute():
            candidate = root / candidate
        primary = candidate
    else:
        prod_path = root / "env" / ".env.production"
        dev_path = root / "env" / ".env"

        if env_name == "production" and prod_path.exists():
            primary = prod_path
        elif under_systemd and prod_path.exists() and not (has_runtime_db or has_runtime_redis):
            # Safety net for worker units that forgot EnvironmentFile.
            primary = prod_path
        elif dev_path.exists():
            primary = dev_path
        elif prod_path.exists():
            primary = prod_path

    loaded_primary = ""
    if primary and primary.exists():
        load_dotenv(primary, override=False)
        loaded_primary = str(primary)

    fallback = root / "env" / ".env"
    if fallback.exists() and (not primary or fallback.resolve() != primary.resolve()):
        load_dotenv(fallback, override=False)

    return loaded_primary
