import time
from typing import Optional, Dict
from datetime import datetime
import os
from dotenv import load_dotenv
import logging

from services.redis_client import get_redis_client

load_dotenv()

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


class RateLimiter:
    """Token bucket rate limiter using Redis"""
    
    # Key prefixes
    SMTP_HOURLY_KEY = "ratelimit:smtp:{smtp_id}:hourly"
    SMTP_DAILY_KEY = "ratelimit:smtp:{smtp_id}:daily"
    DOMAIN_HOURLY_KEY = "ratelimit:domain:{domain}:hourly"
    GLOBAL_MINUTE_KEY = "ratelimit:global:minute"
    
    def __init__(self):
        self.redis = get_redis_client(REDIS_URL)
        
        # Default limits
        self.default_smtp_hourly = 100
        self.default_smtp_daily = 1000
        self.default_domain_hourly = 50  # Max 50 emails/hour to same domain
        self.default_global_minute = 60  # Max 60 emails/minute overall
    
    def _get_window_key(self, key: str, window_seconds: int) -> str:
        """Get time-windowed key"""
        window = int(time.time() / window_seconds)
        return f"{key}:{window}"
    
    def check_and_increment(
        self,
        smtp_id: int,
        to_email: str,
        smtp_hourly_limit: Optional[int] = None,
        smtp_daily_limit: Optional[int] = None
    ) -> Dict[str, any]:
        """
        Check if sending is allowed and increment counters.
        Returns dict with 'allowed' bool and 'reason' if blocked.
        """
        
        domain = to_email.split('@')[-1].lower()
        
        # Get limits
        hourly_limit = smtp_hourly_limit or self.default_smtp_hourly
        daily_limit = smtp_daily_limit or self.default_smtp_daily
        
        # Check all limits
        checks = [
            self._check_limit(
                self.SMTP_HOURLY_KEY.format(smtp_id=smtp_id),
                hourly_limit,
                3600,  # 1 hour window
                f"SMTP {smtp_id} hourly limit ({hourly_limit}/hour)"
            ),
            self._check_limit(
                self.SMTP_DAILY_KEY.format(smtp_id=smtp_id),
                daily_limit,
                86400,  # 24 hour window
                f"SMTP {smtp_id} daily limit ({daily_limit}/day)"
            ),
            self._check_limit(
                self.DOMAIN_HOURLY_KEY.format(domain=domain),
                self.default_domain_hourly,
                3600,
                f"Domain {domain} hourly limit ({self.default_domain_hourly}/hour)"
            ),
            self._check_limit(
                self.GLOBAL_MINUTE_KEY,
                self.default_global_minute,
                60,
                f"Global minute limit ({self.default_global_minute}/min)"
            ),
        ]
        
        # Find first blocked limit
        for check in checks:
            if not check["allowed"]:
                return check
        
        # All checks passed - increment all counters
        self._increment_all(smtp_id, domain)
        
        return {
            "allowed": True,
            "smtp_hourly_remaining": hourly_limit - self._get_count(
                self.SMTP_HOURLY_KEY.format(smtp_id=smtp_id), 3600
            ),
            "smtp_daily_remaining": daily_limit - self._get_count(
                self.SMTP_DAILY_KEY.format(smtp_id=smtp_id), 86400
            ),
        }
    
    def _check_limit(self, key_prefix: str, limit: int, window_seconds: int, description: str) -> Dict:
        """Check if under limit"""
        
        key = self._get_window_key(key_prefix, window_seconds)
        current = int(self.redis.get(key) or 0)
        
        if current >= limit:
            # Calculate retry after
            window_start = int(time.time() / window_seconds) * window_seconds
            retry_after = window_start + window_seconds - int(time.time())
            
            return {
                "allowed": False,
                "reason": description,
                "current": current,
                "limit": limit,
                "retry_after_seconds": retry_after
            }
        
        return {"allowed": True}
    
    def _get_count(self, key_prefix: str, window_seconds: int) -> int:
        """Get current count for a key"""
        key = self._get_window_key(key_prefix, window_seconds)
        return int(self.redis.get(key) or 0)
    
    def _increment_all(self, smtp_id: int, domain: str):
        """Increment all rate limit counters"""
        
        pipe = self.redis.pipeline()
        
        # SMTP hourly
        key = self._get_window_key(self.SMTP_HOURLY_KEY.format(smtp_id=smtp_id), 3600)
        pipe.incr(key)
        pipe.expire(key, 3600)
        
        # SMTP daily
        key = self._get_window_key(self.SMTP_DAILY_KEY.format(smtp_id=smtp_id), 86400)
        pipe.incr(key)
        pipe.expire(key, 86400)
        
        # Domain hourly
        key = self._get_window_key(self.DOMAIN_HOURLY_KEY.format(domain=domain), 3600)
        pipe.incr(key)
        pipe.expire(key, 3600)
        
        # Global minute
        key = self._get_window_key(self.GLOBAL_MINUTE_KEY, 60)
        pipe.incr(key)
        pipe.expire(key, 60)
        
        pipe.execute()
    
    def get_smtp_usage(self, smtp_id: int) -> Dict[str, int]:
        """Get current usage for an SMTP account"""
        
        hourly = self._get_count(self.SMTP_HOURLY_KEY.format(smtp_id=smtp_id), 3600)
        daily = self._get_count(self.SMTP_DAILY_KEY.format(smtp_id=smtp_id), 86400)
        
        return {
            "hourly_used": hourly,
            "daily_used": daily
        }
    
    def get_domain_usage(self, domain: str) -> Dict[str, int]:
        """Get current usage for a domain"""
        
        hourly = self._get_count(self.DOMAIN_HOURLY_KEY.format(domain=domain.lower()), 3600)
        
        return {
            "hourly_used": hourly,
            "hourly_limit": self.default_domain_hourly
        }
    
    def reset_smtp_limits(self, smtp_id: int):
        """Reset limits for an SMTP account (use with caution)"""
        
        # Delete all matching keys
        for key in self.redis.scan_iter(f"ratelimit:smtp:{smtp_id}:*"):
            self.redis.delete(key)
        
        logger.warning(f"Rate limits reset for SMTP {smtp_id}")
    
    def calculate_delay(self, smtp_id: int, to_email: str) -> int:
        """Calculate seconds to wait before sending is allowed"""
        
        result = self.check_and_increment(smtp_id, to_email)
        
        if result["allowed"]:
            return 0
        
        return result.get("retry_after_seconds", 60)
    
    # =========================================================================
    # Simple helper methods for backwards compatibility
    # =========================================================================
    
    def check_rate_limit(self, key: str, limit: int) -> bool:
        """
        Simple rate limit check - returns True if under limit.
        Used by campaign service and worker.
        """
        # Extract type from key (inbox:1 -> inbox, 1)
        parts = key.split(":")
        if len(parts) >= 2 and parts[0] == "inbox":
            inbox_id = parts[1]
            # Check daily limit
            daily_key = self._get_window_key(f"ratelimit:inbox:{inbox_id}:daily", 86400)
            current = int(self.redis.get(daily_key) or 0)
            return current < limit
        
        # Generic key check
        window_key = self._get_window_key(f"ratelimit:{key}", 86400)
        current = int(self.redis.get(window_key) or 0)
        return current < limit
    
    def record_send(self, key: str):
        """
        Record a send for rate limiting.
        Used by campaign service and worker.
        """
        parts = key.split(":")
        if len(parts) >= 2 and parts[0] == "inbox":
            inbox_id = parts[1]
            # Increment daily counter
            daily_key = self._get_window_key(f"ratelimit:inbox:{inbox_id}:daily", 86400)
            pipe = self.redis.pipeline()
            pipe.incr(daily_key)
            pipe.expire(daily_key, 86400)
            pipe.execute()
            return
        
        # Generic key increment
        window_key = self._get_window_key(f"ratelimit:{key}", 86400)
        pipe = self.redis.pipeline()
        pipe.incr(window_key)
        pipe.expire(window_key, 86400)
        pipe.execute()

# Singleton instance
rate_limiter = RateLimiter()
