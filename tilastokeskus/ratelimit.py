"""Rate limiting and bounded exponential backoff.

Yahoo throttles aggressively and does not document the limit (D-21), so this is written to be
conservative by default and, above all, *bounded* — an outage must not turn one collection run
into a process that sleeps for hours holding a systemd unit open (D-42).

Three things are deliberately injectable so behaviour can be asserted directly rather than
inferred: ``sleep``, ``jitter_source``, and the ``Pacer``'s clock. Tests pass deterministic
versions and check exact durations. A backoff that retries the right number of times but sleeps
0.5s where it meant 30s passes a naive test and fails in the field.

Failure is never silent. Every path out of ``call_with_backoff`` is either a 2xx payload or an
exception — there is no status for the caller to forget to check (D-44).
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
AUTH_STATUSES = frozenset({401, 403})


class RequestFailed(RuntimeError):
    """A non-retryable, non-success status. Raised rather than returned (D-44).

    Returning the status alongside the payload would push the check onto every call site, and
    one forgotten check reintroduces exactly the bug this prevents: an error page parsed as
    data. Raising makes silence structurally impossible.
    """

    def __init__(self, status: int, payload: object = None) -> None:
        self.status = status
        self.payload = payload
        super().__init__(f"request failed with status {status}")


class AuthenticationFailed(RequestFailed):
    """401 or 403. Distinct because a revoked refresh token must be unmistakable (D-29)."""

    def __init__(self, status: int, payload: object = None) -> None:
        super().__init__(status, payload)
        message = (
            f"authentication failed with status {status} — the refresh token may have been "
            f"revoked; re-run 'yahoofantasy login'"
        )
        self.args = (message,)


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
    ``max_delay`` — a cap applied *after* jitter, so ``max_delay`` is a genuine maximum.
    Retrying stops at whichever bound is reached first: attempts or cumulative sleep.
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

    # Minimum gap between requests. Yahoo's safe rate is unknown rather than merely
    # unenforced (D-21a), and a backfill has no deadline. Applied by Pacer.
    request_interval: float = 1.0

    def __post_init__(self) -> None:
        """Reject a policy that cannot work, rather than failing obscurely later (D-44)."""
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {self.max_attempts}")
        if self.base_delay <= 0:
            raise ValueError(f"base_delay must be positive, got {self.base_delay}")
        if self.multiplier < 1:
            raise ValueError(f"multiplier must be at least 1, got {self.multiplier}")
        if self.max_delay <= 0:
            raise ValueError(f"max_delay must be positive, got {self.max_delay}")
        if not 0.0 <= self.jitter <= 1.0:
            raise ValueError(f"jitter must be within 0.0-1.0, got {self.jitter}")
        if self.max_cumulative_delay < 0:
            raise ValueError(
                f"max_cumulative_delay must not be negative, got {self.max_cumulative_delay}"
            )
        if self.request_interval < 0:
            raise ValueError(
                f"request_interval must not be negative, got {self.request_interval}"
            )

    def delay_for(self, attempt: int) -> float:
        """Un-jittered delay before the retry following ``attempt`` (zero-based)."""
        if attempt < 0:
            raise ValueError("attempt must be non-negative")
        return min(self.base_delay * (self.multiplier**attempt), self.max_delay)

    def delays(self) -> Iterator[float]:
        """The un-jittered delay sequence, in order, for every permitted retry."""
        for attempt in range(self.max_attempts - 1):
            yield self.delay_for(attempt)


class Pacer:
    """Enforces a minimum gap between requests.

    Elapsed-aware: it sleeps only the remainder of the interval, so time already spent in a
    request or a backoff delay counts toward the gap rather than being added to it.
    """

    def __init__(
        self,
        min_interval: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if min_interval < 0:
            raise ValueError(f"min_interval must not be negative, got {min_interval}")
        self.min_interval = min_interval
        self._sleep = sleep
        self._monotonic = monotonic
        self._last: float | None = None

    def wait(self) -> None:
        """Block until at least ``min_interval`` has passed since the previous request.

        The wait is clamped to ``min_interval``. A clock that jumps backwards would otherwise
        produce a negative elapsed time and therefore a sleep *longer* than the interval —
        unbounded by however far the clock moved. time.monotonic() should never do that, but
        the clock is injectable, and "should never" is not a bound (D-44).
        """
        if self._last is not None and self.min_interval > 0:
            elapsed = self._monotonic() - self._last
            remaining = min(self.min_interval - elapsed, self.min_interval)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._monotonic()


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


def is_success(status: int) -> bool:
    return 200 <= status < 300


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
    pacer: Pacer | None = None,
) -> object:
    """Call ``operation`` until it succeeds or the retry budget is spent.

    ``operation`` returns ``(status, payload)``. Returning the status rather than raising is
    what lets a 999 be handled at all — it never reaches an exception handler (D-43).

    Returns the payload on a 2xx. Every other outcome raises:

    * ``AuthenticationFailed`` on 401 or 403 (D-29)
    * ``RequestFailed`` on any other non-retryable, non-success status
    * ``RateLimitExhausted`` when either ceiling is reached
    """
    policy = policy or BackoffPolicy()
    log = log if log is not None else RetryLog()
    slept = 0.0
    attempts = 0

    while True:
        if pacer is not None:
            pacer.wait()

        attempts += 1
        status, payload = operation()

        if is_success(status):
            return payload

        if not is_retryable(status):
            if status in AUTH_STATUSES:
                raise AuthenticationFailed(status, payload)
            raise RequestFailed(status, payload)

        if attempts >= policy.max_attempts:
            raise RateLimitExhausted(
                attempts, slept, f"status {status}, attempt ceiling reached"
            )

        delay = policy.delay_for(attempts - 1)

        if headers_of is not None:
            server_delay = retry_after_seconds(headers_of(payload))
            if server_delay is not None:
                # Trust the server over our own arithmetic, but never below our own floor:
                # a Retry-After of 0 during throttling should not become a hot loop.
                delay = max(server_delay, delay)

        if policy.jitter:
            delay *= 1.0 + policy.jitter * (2.0 * jitter_source() - 1.0)

        # Cap *after* jitter so max_delay is a genuine maximum, and floor at zero so a
        # malformed jitter source can never produce a negative sleep.
        delay = max(0.0, min(delay, policy.max_delay))

        # Check the budget *before* sleeping. Sleeping a truncated amount and then failing
        # anyway wastes the wait and muddies the log.
        if slept + delay > policy.max_cumulative_delay:
            raise RateLimitExhausted(
                attempts,
                slept,
                f"status {status}, cumulative delay budget "
                f"({policy.max_cumulative_delay:.0f}s) would be exceeded",
            )

        log.record(status, delay, f"status {status}")
        slept += delay
        sleep(delay)
