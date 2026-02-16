import asyncio
import time
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProviderHealth:
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[float] = None
    state: str = "closed"
    latency_history: deque = field(default_factory=lambda: deque(maxlen=20))
    half_open_calls: int = 0
    last_error: Optional[str] = None


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 300,
        half_open_max_calls: int = 2,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self._providers: Dict[str, ProviderHealth] = {}
        self._lock = asyncio.Lock()

    def _get_health(self, provider: str) -> ProviderHealth:
        if provider not in self._providers:
            self._providers[provider] = ProviderHealth()
        return self._providers[provider]

    async def record_success(self, provider: str, latency: float = 0.0) -> None:
        async with self._lock:
            h = self._get_health(provider)
            h.success_count += 1
            h.failure_count = 0
            h.last_error = None
            if latency > 0:
                h.latency_history.append(latency)
            if h.state in ("open", "half_open"):
                logger.info("CircuitBreaker: %s transitioning to closed", provider)
            h.state = "closed"
            h.half_open_calls = 0

    async def record_failure(self, provider: str, error: str) -> None:
        async with self._lock:
            h = self._get_health(provider)
            h.failure_count += 1
            h.last_failure_time = time.time()
            h.last_error = error
            if h.failure_count >= self.failure_threshold and h.state == "closed":
                h.state = "open"
                logger.warning(
                    "CircuitBreaker: %s tripped to OPEN after %d failures",
                    provider,
                    h.failure_count,
                )

    async def can_call(self, provider: str) -> bool:
        async with self._lock:
            h = self._get_health(provider)
            if h.state == "closed":
                return True
            if h.state == "open":
                if h.last_failure_time and (
                    time.time() - h.last_failure_time >= self.recovery_timeout
                ):
                    h.state = "half_open"
                    h.half_open_calls = 0
                    logger.info(
                        "CircuitBreaker: %s transitioning to half_open", provider
                    )
                    return True
                return False
            if h.state == "half_open":
                if h.half_open_calls < self.half_open_max_calls:
                    h.half_open_calls += 1
                    return True
                return False
            return True

    async def get_healthy_providers(self) -> List[str]:
        async with self._lock:
            healthy = []
            now = time.time()
            for name, h in self._providers.items():
                if h.state == "closed":
                    healthy.append(name)
                elif h.state == "half_open":
                    healthy.append(name)
                elif h.state == "open" and h.last_failure_time:
                    if now - h.last_failure_time >= self.recovery_timeout:
                        healthy.append(name)
            return healthy

    async def get_status(self) -> Dict:
        async with self._lock:
            status = {}
            for name, h in self._providers.items():
                avg_lat = (
                    sum(h.latency_history) / len(h.latency_history)
                    if h.latency_history
                    else 0.0
                )
                status[name] = {
                    "state": h.state,
                    "failure_count": h.failure_count,
                    "success_count": h.success_count,
                    "last_failure_time": h.last_failure_time,
                    "last_error": h.last_error,
                    "avg_latency": round(avg_lat, 3),
                    "half_open_calls": h.half_open_calls,
                }
            return status

    async def avg_latency(self, provider: str) -> float:
        async with self._lock:
            h = self._get_health(provider)
            if not h.latency_history:
                return 0.0
            return sum(h.latency_history) / len(h.latency_history)
