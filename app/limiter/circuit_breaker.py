from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

import pybreaker

logger = logging.getLogger(__name__)


class LoggingListener(pybreaker.CircuitBreakerListener):
    """Logs breaker state transitions through the app's structured JSON
    logger, so trips/recoveries are observable without inspecting process
    state directly."""

    def state_change(
        self,
        cb: pybreaker.CircuitBreaker,
        old_state: pybreaker.CircuitBreakerState | None,
        new_state: pybreaker.CircuitBreakerState,
    ) -> None:
        old_name = old_state.name if old_state is not None else "none"
        logger.warning(
            "circuit_breaker_state_change breaker=%s from=%s to=%s fail_counter=%s",
            cb.name,
            old_name,
            new_state.name,
            cb.fail_counter,
        )


class AsyncCircuitBreaker:
    """Async-compatible wrapper around pybreaker.CircuitBreaker.

    pybreaker's own call_async() requires tornado (it lazily imports
    tornado.gen internally) -- tornado isn't a dependency of this project
    and adding one just to satisfy a coroutine-wrapping shim would mean
    pulling in a second web framework, which the project's stack rules
    out. Instead this uses CircuitBreaker.calling(), a plain, fully
    public sync context manager: entering it runs the same
    closed/open/half-open decision pybreaker's own call() does (raising
    pybreaker.CircuitBreakerError immediately if open and the cooldown
    hasn't elapsed, or transitioning to half-open first), and exiting it
    records success/failure -- but since it's just a context manager, the
    code inside the `with` block is free to `await`. pybreaker remains
    the sole owner of the state machine; nothing here reimplements it.
    """

    def __init__(self, breaker: pybreaker.CircuitBreaker, call_timeout_seconds: float) -> None:
        self._breaker = breaker
        self._call_timeout_seconds = call_timeout_seconds

    async def call(self, func: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        with self._breaker.calling():
            return await asyncio.wait_for(func(*args, **kwargs), timeout=self._call_timeout_seconds)
