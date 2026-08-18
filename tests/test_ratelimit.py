"""Tests for bounded backoff.

These assert the *durations*, not merely that a retry happened. A backoff that retries the
right number of times but computes 0.5s where it meant 30s passes a behavioural test and fails
in the field (D-42).
"""

import pytest

from tilastokeskus.ratelimit import (
    BackoffPolicy,
    RateLimitExhausted,
    RetryLog,
    call_with_backoff,
    is_retryable,
    retry_after_seconds,
)

# Deterministic: jitter disabled so delays are exact.
EXACT = BackoffPolicy(base_delay=2.0, multiplier=2.0, max_delay=60.0, max_attempts=5, jitter=0.0)


class Recorder:
    """Stands in for time.sleep, capturing every duration."""

    def __init__(self):
        self.slept: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


def responder(statuses, payload="ok"):
    """An operation returning the given statuses in order, then 200."""
    seq = list(statuses)

    def operation():
        status = seq.pop(0) if seq else 200
        return status, payload

    return operation


# --- delay arithmetic -------------------------------------------------------------------


def test_delay_sequence_is_exact():
    assert [EXACT.delay_for(n) for n in range(5)] == [2.0, 4.0, 8.0, 16.0, 32.0]


def test_delay_is_capped_at_max_delay():
    policy = BackoffPolicy(base_delay=2.0, multiplier=2.0, max_delay=10.0, jitter=0.0)
    assert [policy.delay_for(n) for n in range(6)] == [2.0, 4.0, 8.0, 10.0, 10.0, 10.0]


def test_delays_iterator_covers_every_permitted_retry():
    # 5 attempts means 4 retries, so 4 delays.
    assert list(EXACT.delays()) == [2.0, 4.0, 8.0, 16.0]


def test_negative_attempt_rejected():
    with pytest.raises(ValueError):
        EXACT.delay_for(-1)


# --- actual sleeping --------------------------------------------------------------------


def test_sleeps_exactly_the_computed_delays():
    sleeper = Recorder()
    result = call_with_backoff(responder([999, 999, 429]), EXACT, sleep=sleeper, jitter_source=lambda: 0.5)
    assert result == "ok"
    assert sleeper.slept == [2.0, 4.0, 8.0]


def test_no_sleep_when_first_call_succeeds():
    sleeper = Recorder()
    call_with_backoff(responder([]), EXACT, sleep=sleeper)
    assert sleeper.slept == []


def test_success_returns_payload_without_retrying():
    sleeper = Recorder()
    assert call_with_backoff(responder([], payload={"a": 1}), EXACT, sleep=sleeper) == {"a": 1}
    assert sleeper.slept == []


# --- bounds -----------------------------------------------------------------------------


def test_attempt_ceiling_fails_loudly():
    sleeper = Recorder()
    with pytest.raises(RateLimitExhausted) as exc:
        call_with_backoff(responder([999] * 10), EXACT, sleep=sleeper)

    assert exc.value.attempts == 5
    # 4 retries between 5 attempts.
    assert sleeper.slept == [2.0, 4.0, 8.0, 16.0]
    assert "attempt ceiling" in str(exc.value)


def test_cumulative_budget_stops_before_sleeping_past_it():
    """The budget is checked before the sleep, not after — a truncated wait then failing
    anyway would waste the wait and muddy the log."""
    policy = BackoffPolicy(
        base_delay=10.0, multiplier=2.0, max_delay=100.0,
        max_attempts=10, max_cumulative_delay=25.0, jitter=0.0,
    )
    sleeper = Recorder()
    with pytest.raises(RateLimitExhausted) as exc:
        call_with_backoff(responder([999] * 10), policy, sleep=sleeper)

    # 10 then 20 would total 30, over the 25s budget, so the second sleep never happens.
    assert sleeper.slept == [10.0]
    assert exc.value.slept == 10.0
    assert "cumulative delay budget" in str(exc.value)


def test_an_outage_cannot_pin_the_run_open():
    """The whole point of the ceilings: total sleep is bounded regardless of how long the
    remote side stays broken."""
    policy = BackoffPolicy(max_attempts=100, max_cumulative_delay=60.0, jitter=0.0)
    sleeper = Recorder()
    with pytest.raises(RateLimitExhausted):
        call_with_backoff(responder([999] * 500), policy, sleep=sleeper)
    assert sum(sleeper.slept) <= 60.0


# --- status handling --------------------------------------------------------------------


@pytest.mark.parametrize("status", [999, 429, 500, 502, 503, 504])
def test_retryable_statuses(status):
    assert is_retryable(status)


@pytest.mark.parametrize("status", [200, 201, 301, 400, 401, 403, 404])
def test_non_retryable_statuses(status):
    assert not is_retryable(status)


def test_999_is_retried_even_though_it_never_raises():
    """Yahoo's throttle status sits outside the 400-599 range that raise_for_status reacts
    to, so it arrives as an ordinary response. Keying retries off exceptions would miss it
    entirely (D-43)."""
    sleeper = Recorder()
    assert call_with_backoff(responder([999]), EXACT, sleep=sleeper) == "ok"
    assert sleeper.slept == [2.0]


def test_auth_failure_is_not_retried():
    """A revoked refresh token must fail immediately and loudly (D-29), not be retried into
    looking like a throttle."""
    sleeper = Recorder()
    calls = []

    def operation():
        calls.append(1)
        return 401, "unauthorized"

    assert call_with_backoff(operation, EXACT, sleep=sleeper) == "unauthorized"
    assert len(calls) == 1
    assert sleeper.slept == []


# --- Retry-After ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"Retry-After": "30"}, 30.0),
        ({"retry-after": "30"}, 30.0),      # case-insensitive
        ({"Retry-After": "0"}, 0.0),
        ({"Retry-After": "bogus"}, None),   # HTTP-date form ignored rather than mis-parsed
        ({"Retry-After": "-5"}, None),
        ({}, None),
        (None, None),
    ],
)
def test_retry_after_parsing(headers, expected):
    assert retry_after_seconds(headers) == expected


def test_retry_after_overrides_a_shorter_computed_delay():
    sleeper = Recorder()
    call_with_backoff(
        responder([429]), EXACT, sleep=sleeper,
        headers_of=lambda _: {"Retry-After": "45"},
    )
    assert sleeper.slept == [45.0]


def test_retry_after_never_shortens_our_own_floor():
    """A Retry-After of 0 during throttling must not become a hot loop."""
    sleeper = Recorder()
    call_with_backoff(
        responder([429]), EXACT, sleep=sleeper,
        headers_of=lambda _: {"Retry-After": "0"},
    )
    assert sleeper.slept == [2.0]


# --- jitter -----------------------------------------------------------------------------


def test_jitter_stays_within_its_band():
    policy = BackoffPolicy(base_delay=10.0, max_attempts=2, jitter=0.2)

    for source, expected in [(lambda: 0.0, 8.0), (lambda: 1.0, 12.0), (lambda: 0.5, 10.0)]:
        sleeper = Recorder()
        with pytest.raises(RateLimitExhausted):
            call_with_backoff(responder([999] * 5), policy, sleep=sleeper, jitter_source=source)
        assert sleeper.slept == [pytest.approx(expected)]


# --- logging ----------------------------------------------------------------------------


def test_every_wait_is_logged_with_its_cause():
    log = RetryLog()
    call_with_backoff(responder([999, 503]), EXACT, sleep=Recorder(), log=log)

    assert [status for status, _, _ in log.waits] == [999, 503]
    assert [delay for _, delay, _ in log.waits] == [2.0, 4.0]
    assert log.total_slept == 6.0


def test_log_is_empty_when_nothing_retried():
    log = RetryLog()
    call_with_backoff(responder([]), EXACT, sleep=Recorder(), log=log)
    assert log.waits == []
    assert log.total_slept == 0.0
