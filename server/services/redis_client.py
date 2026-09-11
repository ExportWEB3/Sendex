"""Shared Redis client access for stateless API and service operations."""

import os
from functools import lru_cache
from typing import Optional

import redis

DEFAULT_REDIS_URL = "redis://localhost:6379"


@lru_cache(maxsize=8)
def _client_for_url(url: str) -> redis.Redis:
    """Create one thread-safe redis-py client (and pool) per configured URL."""
    return redis.from_url(url, decode_responses=True)


def get_redis_client(url: Optional[str] = None) -> redis.Redis:
    """Return a cached client for the explicit URL or current environment."""
    return _client_for_url(url or os.getenv("REDIS_URL", DEFAULT_REDIS_URL))
