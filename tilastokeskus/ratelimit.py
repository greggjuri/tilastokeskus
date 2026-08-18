"""Rate limiting and bounded exponential backoff.

Yahoo throttles aggressively and does not document the limit (D-21), so this is written to be
conservative by default and, above all, *bounded* — an outage must not turn one collection run
into a process that sleeps for hours holding a systemd unit open (D-42).

Two things here are deliberately injectable so the delay arithmetic can be asserted directly
rather than inferred from behaviour: ``sleep`` and ``jitter_source``. Tests pass deterministic
versions and check the exact sequence of durations. A backoff that retries the right number of
times but sleeps 0.5s where it meant 30s passes a naive test and fails in the field.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

# Yahoo returns 999 for throttling. Note this is outside the 400-599 range that
# requests' raise_for_status() reacts to, so it arrives as an ordinary response and must be
# inspected explicitly — a retry layer keyed off exceptions alone would never see it (D-43).
THROTTLE_STATUS = 999
RETRYABLE_STATUSES = frozenset({THROTTLE_STATUS, 429, 500, 502, 503, 504})


class RateLimitExhausted(RuntimeError):
    """Raised when the retry budget is spent. Deliberately loud (D-38).

    Carries the numbers needed to tell the two failure modes apart: hitting the attempt
    ceiling quickly is a different problem from grinding through the whole delay budget.
    """

    def __init__(self, attempts: int, slept: float, reason: str) -> None:
        self.attempts = attempts
        self.slept = slept
        super().__init__(
            f"giving up after {attempts} attempt(s) and {slept:.1f}s of backoff: {reason}"
        )


@dataclass(frozen=True)
class BackoffPolicy:
    """Bounded exponential backoff.

    Delay for attempt *n* (zero-based) is ``base_delay * multiplier**n``, capped at
    ``max_delay``. Retrying stops at whichever bound is reached first — attempts or
    cumulative sleep.
    """

    base_delay: float = 2.0
    multiplier: float = 2.0
    max_delay: float = 60.0

    # Both ceilings exist because they fail differently. max_attempts bounds a fast-failing
    # endpoint; max_cumulative_delay bounds a slow one that would otherwise pin the run open.
    max_attempts: int = 5
    max_cumulative_delay: float = 300.0

    # Fraction of each delay randomised, to avoid eight leagues retrying in lockstep.
    # 0.0 makes delays exactly reproducible, which is what the tests use.
    jitter: float = 0.2

    # A deliberate pause between *successful* requests. Yahoo's safe rate is unknown rather
    # than merely unenforced (D-21a), and a backfill has no deadline.
    request_interval: float = 1.0

    def delay_for(self, attempt: int) -> float:
        """Un-jittered delay before the retry following ``attempt`` (zero-based)."""
        if attempt < 0:
            raise ValueError("attempt must be non-negative")
        return min(self.base_delay * (self.multiplier**attempt), self.max_delay)

    def delays(self) -> Iterator[float]:
        """The un-jittered delay sequence, in order, for every permitted retry."""
        for attempt in range(self.max_attempts - 1):
            yield self.delay_for(attempt)


@dataclass
class RetryLog:
    """What actually happened. A backoff that silently works is indistinguishable from one
    that never fired (D-21a), so every wait is recorded with its cause."""

    waits: list[tuple[int, float, str]] = field(default_factory=list)

    @property
    def total_slept(self) -> float:
        return sum(w for _, w, _ in self.waits)

    def record(self, status: int, delay: float, reason: str) -> None:
        self.waits.append((status, delay, reason))


def is_retryable(status: int) -> bool:
    return status in RETRYABLE_STATUSES


def retry_after_seconds(headers: dict | None) -> float | None:
    """Honour a Retry-After header when the server sends one. Seconds form only; the HTTP-date
    form is not used by Yahoo and guessing at a date parse would be worse than ignoring it."""
    if not headers:
        return None
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                return None
            return seconds if seconds >= 0 else None
    return None


def call_with_backoff(
    operation: Callable[[], tuple[int, object]],
    policy: BackoffPolicy | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    jitter_source: Callable[[], float] = random.random,
    headers_of: Callable[[object], dict | None] | None = None,
    log: RetryLog | None = None,
) -> object:
    """Call ``operation`` until it returns a non-retryable status or the budget is spent.

    ``operation`` returns ``(status, payload)``. Returning the status rather than raising is
    what lets a 999 be handled at all — it never reaches an exception handler (D-43).

    Raises RateLimitExhausted when either ceiling is reached.
    """
    policy = policy or BackoffPolicy()
    log = log if log is not None else RetryLog()
    slept = 0.0

    for attempt in range(policy.max_attempts):
        status, payload = operation()

        if not is_retryable(status):
            return payload

        if attempt == policy.max_attempts - 1:
            raise RateLimitExhausted(
                attempt + 1, slept, f"status {status}, attempt ceiling reached"
            )

        delay = policy.delay_for(attempt)

        if headers_of is not None:
            server_delay = retry_after_seconds(headers_of(payload))
            if server_delay is not None:
                # Trust the server over our own arithmetic, but never below our own floor:
                # a Retry-After of 0 during throttling should not become a hot loop.
                delay = max(server_delay, delay)

        if policy.jitter:
            delay *= 1.0 + policy.jitter * (2.0 * jitter_source() - 1.0)

        # Check the budget *before* sleeping. Sleeping a truncated amount and then failing
        # anyway wastes the wait and muddies the log.
        if slept + delay > policy.max_cumulative_delay:
            raise RateLimitExhausted(
                attempt + 1,
                slept,
                f"status {status}, cumulative delay budget "
                f"({policy.max_cumulative_delay:.0f}s) would be exceeded",
            )

        log.record(status, delay, f"status {status}")
        slept += delay
        sleep(delay)

    raise AssertionError("unreachable: loop always returns or raises")
