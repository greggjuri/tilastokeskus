"""Tests for bounded backoff.

These assert the *durations*, not merely that a retry happened. A backoff that retries the
right number of times but computes 0.5s where it meant 30s passes a behavioural test and fails
in the field (D-42).
"""

import pytest

from tilastokeskus.ratelimit import (
    AuthenticationFailed,
    BackoffPolicy,
    Pacer,
    RateLimitExhausted,
    RequestFailed,
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


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_raises_immediately(status):
    """A revoked refresh token must fail immediately and loudly (D-29), not be retried into
    looking like a throttle — and not returned as though it were data (D-44)."""
    sleeper = Recorder()
    calls = []

    def operation():
        calls.append(1)
        return status, "unauthorized"

    with pytest.raises(AuthenticationFailed) as exc:
        call_with_backoff(operation, EXACT, sleep=sleeper)

    assert exc.value.status == status
    assert "revoked" in str(exc.value)
    assert len(calls) == 1      # not retried
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


# --- contract: what the function returns and accepts at its boundary --------------------
#
# The original suite tested the arithmetic thoroughly and never tested the contract. Every
# defect it missed was about a return value or an accepted input, not a computation (D-44).


@pytest.mark.parametrize("status", [200, 201, 204, 299])
def test_success_statuses_return_the_payload(status):
    assert call_with_backoff(lambda: (status, "body"), EXACT, sleep=Recorder()) == "body"


@pytest.mark.parametrize("status", [400, 404, 418, 451])
def test_client_errors_raise_rather_than_returning(status):
    """The defect this replaces: a 404 was returned exactly like a 200, so an error page
    would have been parsed as data with nothing to signal otherwise."""
    with pytest.raises(RequestFailed) as exc:
        call_with_backoff(lambda: (status, "error page"), EXACT, sleep=Recorder())
    assert exc.value.status == status
    assert exc.value.payload == "error page"


@pytest.mark.parametrize("status", [301, 302, 307])
def test_redirects_are_not_treated_as_success(status):
    with pytest.raises(RequestFailed):
        call_with_backoff(lambda: (status, "redirect"), EXACT, sleep=Recorder())


def test_auth_failure_is_a_request_failure():
    """Callers that only catch RequestFailed must still catch auth failures."""
    with pytest.raises(RequestFailed):
        call_with_backoff(lambda: (401, "x"), EXACT, sleep=Recorder())


# --- contract: policy validation --------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"max_attempts": -1},
        {"base_delay": 0},
        {"base_delay": -1.0},
        {"multiplier": 0.5},
        {"max_delay": 0},
        {"jitter": -0.1},
        {"jitter": 1.5},
        {"max_cumulative_delay": -1.0},
        {"request_interval": -1.0},
    ],
)
def test_invalid_policies_are_rejected_at_construction(kwargs):
    with pytest.raises(ValueError):
        BackoffPolicy(**kwargs)


def test_minimum_viable_policy_is_accepted():
    policy = BackoffPolicy(max_attempts=1, jitter=0.0)
    with pytest.raises(RateLimitExhausted) as exc:
        call_with_backoff(responder([999]), policy, sleep=Recorder())
    assert exc.value.attempts == 1


# --- contract: max_delay is a genuine maximum -------------------------------------------


def test_jitter_cannot_push_a_delay_past_max_delay():
    """Jitter used to be applied after the cap, so max_delay=60 could sleep 72s."""
    policy = BackoffPolicy(base_delay=100.0, max_delay=60.0, max_attempts=3, jitter=0.2)
    sleeper = Recorder()
    with pytest.raises(RateLimitExhausted):
        call_with_backoff(responder([999] * 5), policy, sleep=sleeper, jitter_source=lambda: 1.0)
    assert all(d <= 60.0 for d in sleeper.slept), sleeper.slept


def test_delay_is_never_negative():
    """A malformed jitter source must not produce a negative sleep, which real time.sleep
    rejects with ValueError."""
    policy = BackoffPolicy(base_delay=10.0, max_attempts=3, jitter=1.0)
    sleeper = Recorder()
    with pytest.raises(RateLimitExhausted):
        call_with_backoff(responder([999] * 5), policy, sleep=sleeper, jitter_source=lambda: -5.0)
    assert all(d >= 0.0 for d in sleeper.slept), sleeper.slept


# --- pacing -----------------------------------------------------------------------------


class FakeClock:
    """A monotonic clock that advances when slept on."""

    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_pacer_does_not_wait_before_the_first_request():
    clock = FakeClock()
    Pacer(1.0, sleep=clock.sleep, monotonic=clock.monotonic).wait()
    assert clock.slept == []


def test_pacer_waits_the_remaining_interval():
    clock = FakeClock()
    pacer = Pacer(1.0, sleep=clock.sleep, monotonic=clock.monotonic)
    pacer.wait()
    clock.advance(0.25)     # request took 0.25s
    pacer.wait()
    assert clock.slept == [0.75]


def test_pacer_does_not_wait_when_enough_time_has_passed():
    """Elapsed-aware: a slow request already satisfies the interval."""
    clock = FakeClock()
    pacer = Pacer(1.0, sleep=clock.sleep, monotonic=clock.monotonic)
    pacer.wait()
    clock.advance(5.0)
    pacer.wait()
    assert clock.slept == []


def test_pacer_interval_of_zero_never_waits():
    clock = FakeClock()
    pacer = Pacer(0.0, sleep=clock.sleep, monotonic=clock.monotonic)
    pacer.wait()
    pacer.wait()
    assert clock.slept == []


def test_pacer_rejects_a_negative_interval():
    with pytest.raises(ValueError):
        Pacer(-1.0)


def test_backoff_applies_the_pacer_to_every_attempt():
    """request_interval used to be dead config — declared, documented, never applied."""
    clock = FakeClock()
    pacer = Pacer(1.0, sleep=clock.sleep, monotonic=clock.monotonic)
    call_with_backoff(responder([999, 999]), EXACT, sleep=clock.sleep, pacer=pacer)
    # Three attempts, two backoff sleeps. Pacing adds nothing here because each backoff
    # delay already exceeds the 1.0s interval — the gap is satisfied, not stacked on top.
    assert clock.slept == [2.0, 4.0]


def test_pacer_gaps_successive_successful_calls():
    clock = FakeClock()
    pacer = Pacer(1.0, sleep=clock.sleep, monotonic=clock.monotonic)
    for _ in range(3):
        call_with_backoff(responder([]), EXACT, sleep=clock.sleep, pacer=pacer)
    assert clock.slept == [1.0, 1.0]


# --- no unreachable claims anywhere -----------------------------------------------------


def test_no_unreachable_assertions_in_the_package():
    """`raise AssertionError("unreachable")` was reachable with max_attempts=0. Any other
    claim of impossibility in this package is the same class of bug (D-44)."""
    import pathlib

    import tilastokeskus

    root = pathlib.Path(tilastokeskus.__file__).parent
    offenders = []
    for path in root.rglob("*.py"):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            lowered = line.lower()
            if "unreachable" in lowered and ("assert" in lowered or "raise" in lowered):
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")

    assert not offenders, "unreachable claims found:\n" + "\n".join(offenders)


def test_pacer_wait_is_clamped_when_the_clock_goes_backwards():
    """A backwards clock made elapsed negative, so the wait exceeded min_interval by however
    far the clock moved — a 1s interval slept 101s. monotonic() should never go backwards,
    but the clock is injectable and "should never" is not a bound (D-44)."""
    slept = []
    now = [1000.0]
    pacer = Pacer(1.0, sleep=slept.append, monotonic=lambda: now[0])

    pacer.wait()
    now[0] = 900.0          # jump back 100 seconds
    pacer.wait()

    assert slept == [1.0]


def test_pacer_never_sleeps_longer_than_its_interval():
    """The general form of the above, across a range of clock movements."""
    for jump in [-1000.0, -1.0, 0.0, 0.5, 2.0]:
        slept = []
        now = [500.0]
        pacer = Pacer(2.0, sleep=slept.append, monotonic=lambda clock=now: clock[0])
        pacer.wait()
        now[0] += jump
        pacer.wait()
        assert all(0.0 < s <= 2.0 for s in slept), f"jump={jump}: {slept}"


def test_pacer_last_timestamp_recovers_after_a_backwards_jump():
    """After the clamp, normal pacing resumes from the new clock reading rather than staying
    wedged against the stale one."""
    slept = []
    now = [1000.0]
    pacer = Pacer(1.0, sleep=slept.append, monotonic=lambda: now[0])

    pacer.wait()
    now[0] = 900.0
    pacer.wait()            # clamped to 1.0
    now[0] = 905.0          # 5s later on the new timeline
    pacer.wait()            # plenty elapsed, so no wait

    assert slept == [1.0]
