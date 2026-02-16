import time
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

class RateLimitService:
    def __init__(self):
        self._buckets = defaultdict(list)
        self._config = {
            "default": {"max_requests": 30, "window_seconds": 60},
            "shell": {"max_requests": 10, "window_seconds": 60},
            "goal": {"max_requests": 3, "window_seconds": 300},
            "extract": {"max_requests": 5, "window_seconds": 300},
            "cron": {"max_requests": 10, "window_seconds": 60},
        }

    def check(self, user_id: int, command: str = "default") -> tuple[bool, str]:
        """Check if user can execute command. Returns (allowed, reason)."""
        config = self._config.get(command, self._config["default"])
        key = f"{user_id}:{command}"
        now = time.time()
        window = config["window_seconds"]
        max_req = config["max_requests"]
        
        self._buckets[key] = [t for t in self._buckets[key] if now - t < window]
        
        if len(self._buckets[key]) >= max_req:
            remaining = int(window - (now - self._buckets[key][0]))
            return False, f"Rate limited on /{command}. Try again in {remaining}s."
        
        self._buckets[key].append(now)
        return True, ""

    def get_status(self, user_id: int) -> dict:
        """Get rate limit status for a user."""
        now = time.time()
        status = {}
        for cmd, config in self._config.items():
            key = f"{user_id}:{cmd}"
            window = config["window_seconds"]
            recent = [t for t in self._buckets.get(key, []) if now - t < window]
            status[cmd] = {"used": len(recent), "max": config["max_requests"], "window": window}
        return status

rate_limiter = RateLimitService()
