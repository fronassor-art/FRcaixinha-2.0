from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app.services.late_charge_v1 import financial_civil_date
from app.worker import main as worker_main
from app.worker import tasks


class RedisError(Exception):
    pass


class RedisConnectionError(RedisError):
    pass


class RedisTimeoutError(RedisError):
    pass


class RedisAuthenticationError(RedisConnectionError):
    pass


class RedisAuthorizationError(RedisConnectionError):
    pass


class RedisBusyLoadingError(RedisConnectionError):
    pass


class RedisMaxConnectionsError(RedisConnectionError):
    pass


class RedisReadOnlyError(RedisError):
    pass


class RedisInvalidResponse(RedisError):
    pass


class StopWorkerLoop(BaseException):
    pass


@pytest.fixture
def redis_dispatch(monkeypatch):
    calls = []
    lock_result = True
    lock_error = None

    class Client:
        def set(self, key, value, *, nx=False, ex=None):
            calls.append((key, value, nx, ex))
            if nx:
                if lock_error is not None:
                    raise lock_error
                return lock_result
            return True

    client = Client()
    errors = SimpleNamespace(
        ConnectionError=RedisConnectionError,
        TimeoutError=RedisTimeoutError,
        AuthenticationError=RedisAuthenticationError,
        AuthorizationError=RedisAuthorizationError,
        BusyLoadingError=RedisBusyLoadingError,
        MaxConnectionsError=RedisMaxConnectionsError,
        ReadOnlyError=RedisReadOnlyError,
        InvalidResponse=RedisInvalidResponse,
    )
    fake_redis = SimpleNamespace(from_url=lambda *a, **k: client, exceptions=errors)
    monkeypatch.setattr(worker_main, "redis", fake_redis)

    def configure(*, result=True, error=None):
        nonlocal lock_result, lock_error
        lock_result = result
        lock_error = error

    return calls, configure


def _run_loop(monkeypatch, scheduled_times, daily_task, cycle_task):
    times = iter(scheduled_times)

    class Clock:
        @classmethod
        def now(cls, tz=None):
            assert tz == timezone.utc
            return next(times, scheduled_times[-1])

    monkeypatch.setattr(worker_main, "datetime", Clock)
    sleep_count = 0

    def stop_after_slots(_seconds):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= len(scheduled_times):
            raise StopWorkerLoop

    monkeypatch.setattr(worker_main.time, "sleep", stop_after_slots)
    monkeypatch.setattr(worker_main, "run_daily_tasks", daily_task)
    monkeypatch.setattr(worker_main, "run_cycle_participation_tasks", cycle_task)
    with pytest.raises(StopWorkerLoop):
        worker_main.main()
    return sleep_count


@pytest.mark.parametrize(
    ("slot", "expected_job"),
    [
        (datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc), "daily"),
        (datetime(2026, 9, 28, 4, 5, tzinfo=timezone.utc), "cycle"),
    ],
)
def test_redis_lock_true_runs_current_slot(monkeypatch, redis_dispatch, slot, expected_job):
    calls, _ = redis_dispatch
    ran = []
    _run_loop(
        monkeypatch,
        [slot],
        lambda **kwargs: ran.append(("daily", kwargs["scheduled_for"])),
        lambda **kwargs: ran.append(("cycle", kwargs["scheduled_for"])) or {},
    )
    assert ran == [(expected_job, slot)]
    lock_calls = [call for call in calls if call[2] is True]
    assert len(lock_calls) == 1
    key, value, nx, ttl = lock_calls[0]
    assert value == "1" and nx is True and ttl == 86400
    assert key == (
        f"frcaixinha:{'daily' if expected_job == 'daily' else 'cycle-daily'}:"
        f"{slot.date().isoformat()}"
    )


def test_redis_lock_false_skips_current_slot(monkeypatch, redis_dispatch, caplog):
    _, configure = redis_dispatch
    configure(result=False)
    ran = []
    _run_loop(
        monkeypatch,
        [datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)],
        lambda **kwargs: ran.append(kwargs),
        lambda **kwargs: ran.append(kwargs),
    )
    assert ran == []
    assert not any("redis_lock_unavailable" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(
    "error",
    [
        RedisConnectionError("offline redis://user:secret@example.invalid"),
        RedisTimeoutError("timeout redis://user:secret@example.invalid"),
    ],
)
def test_redis_transient_failure_attempts_current_slot(monkeypatch, redis_dispatch, error, caplog):
    _, configure = redis_dispatch
    configure(error=error)
    slot = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)
    ran = []
    _run_loop(
        monkeypatch,
        [slot],
        lambda **kwargs: ran.append(kwargs["scheduled_for"]),
        lambda **kwargs: None,
    )
    assert ran == [slot]
    warning = next(record for record in caplog.records if "redis_lock_unavailable" in record.getMessage())
    message = warning.getMessage()
    assert "job=worker_daily_tasks" in message
    assert f"slot={slot.isoformat()}" in message
    assert f"error_type={type(error).__name__}" in message
    assert "redis://" not in message
    assert "secret" not in message


@pytest.mark.parametrize(
    "error",
    [
        RedisAuthenticationError("bad auth"),
        RedisAuthorizationError("forbidden"),
        RedisBusyLoadingError("loading"),
        RedisMaxConnectionsError("pool exhausted"),
        RedisReadOnlyError("readonly"),
        RedisInvalidResponse("bad protocol"),
        ValueError("bad Redis URL"),
        TypeError("programming error"),
    ],
)
def test_unapproved_redis_errors_do_not_fail_open(monkeypatch, redis_dispatch, error):
    calls, configure = redis_dispatch
    configure(error=error)
    with pytest.raises(type(error)):
        worker_main.acquire_daily_lock()
    assert any(nx for _, _, nx, _ in calls)


def test_fail_open_preserves_scheduled_for_and_belem_financial_date(monkeypatch, redis_dispatch):
    _, configure = redis_dispatch
    configure(error=RedisConnectionError("offline"))
    scheduled_for = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)
    observed = []
    _run_loop(
        monkeypatch,
        [scheduled_for],
        lambda **kwargs: observed.append((kwargs["scheduled_for"], financial_civil_date(kwargs["scheduled_for"]))),
        lambda **kwargs: None,
    )
    assert observed == [(scheduled_for, datetime(2026, 9, 27).date())]


def test_cycle_fail_open_preserves_belem_financial_date(monkeypatch, redis_dispatch):
    _, configure = redis_dispatch
    configure(error=RedisTimeoutError("timeout"))
    scheduled_for = datetime(2026, 9, 28, 4, 5, tzinfo=timezone.utc)
    observed = []
    _run_loop(
        monkeypatch,
        [scheduled_for],
        lambda **kwargs: None,
        lambda **kwargs: observed.append((kwargs["scheduled_for"], financial_civil_date(kwargs["scheduled_for"]))),
    )
    assert observed == [(scheduled_for, datetime(2026, 9, 28).date())]


def test_redis_outage_does_not_kill_worker_loop(monkeypatch, redis_dispatch):
    _, configure = redis_dispatch
    configure(error=RedisConnectionError("offline"))
    slots = [
        datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc),
        datetime(2026, 9, 28, 0, 6, tzinfo=timezone.utc),
    ]
    ran = []
    sleeps = _run_loop(
        monkeypatch,
        slots,
        lambda **kwargs: ran.append(kwargs["scheduled_for"]),
        lambda **kwargs: None,
    )
    assert ran == [slots[0]]
    assert sleeps == 2


def test_postgres_failure_after_redis_fail_open_has_no_financial_effect(monkeypatch, redis_dispatch, caplog):
    _, configure = redis_dispatch
    configure(error=RedisConnectionError("offline"))
    scheduled_for = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)
    effect_calls = []

    def unavailable_session():
        raise OperationalError("connect", {}, OSError("postgres unavailable"))

    monkeypatch.setattr(tasks, "SessionLocal", unavailable_session)
    monkeypatch.setattr(tasks, "_execute_daily_effects", lambda *a, **k: effect_calls.append(True))
    _run_loop(
        monkeypatch,
        [scheduled_for],
        tasks.run_daily_tasks,
        lambda **kwargs: None,
    )
    assert effect_calls == []
    assert any(record.message == "scheduled_task_failed" for record in caplog.records)


def test_startup_after_slot_does_not_create_historical_execution(monkeypatch, redis_dispatch):
    calls, _ = redis_dispatch
    late_times = [
        datetime(2026, 9, 28, 0, 6, tzinfo=timezone.utc),
        datetime(2026, 9, 28, 4, 6, tzinfo=timezone.utc),
    ]
    ran = []
    _run_loop(
        monkeypatch,
        late_times,
        lambda **kwargs: ran.append(kwargs),
        lambda **kwargs: ran.append(kwargs),
    )
    assert ran == []
    assert not [call for call in calls if call[2] is True]


def test_redis_module_missing_does_not_silently_fail_open(monkeypatch):
    monkeypatch.setattr(worker_main, "redis", None)
    with pytest.raises(RuntimeError, match="redis_client_unavailable"):
        worker_main.acquire_daily_lock()


def test_redis_py_640_exception_inheritance():
    redis_py = pytest.importorskip("redis")
    assert redis_py.__version__ == "6.4.0"
    errors = redis_py.exceptions
    assert issubclass(errors.AuthenticationError, errors.ConnectionError)
    assert issubclass(errors.AuthorizationError, errors.ConnectionError)
    assert issubclass(errors.BusyLoadingError, errors.ConnectionError)
    assert issubclass(errors.MaxConnectionsError, errors.ConnectionError)
    assert not issubclass(errors.ReadOnlyError, errors.ConnectionError)
