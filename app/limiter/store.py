import time
from dataclasses import dataclass


@dataclass
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_at: float
    retry_after: float | None


# (policy_name, key) -> list of request timestamps. Naive sliding-window-log
# kept in a single process-local dict: not shared across workers/processes
# and resets on restart. Deliberate for this phase — Phase 2 replaces this
# module with a Redis-backed store behind the same check() contract.
_requests: dict[tuple[str, str], list[float]] = {}


def check(policy_name: str, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
    now = time.time()
    window_start = now - window_seconds
    bucket_key = (policy_name, key)

    timestamps = [t for t in _requests.get(bucket_key, []) if t > window_start]

    if len(timestamps) >= limit:
        _requests[bucket_key] = timestamps
        reset_at = timestamps[0] + window_seconds
        return RateLimitDecision(
            allowed=False,
            limit=limit,
            remaining=0,
            reset_at=reset_at,
            retry_after=max(reset_at - now, 0),
        )

    timestamps.append(now)
    _requests[bucket_key] = timestamps
    return RateLimitDecision(
        allowed=True,
        limit=limit,
        remaining=limit - len(timestamps),
        reset_at=timestamps[0] + window_seconds,
        retry_after=None,
    )
