import datetime as dt

from src.health import HealthStore, empty_cycle, utc_now


def test_health_roundtrip_preserves_circuit_breaker(tmp_path):
    path = tmp_path / "health.json"
    blocked_until = utc_now() + dt.timedelta(hours=2)
    store = HealthStore(path)
    store.write(
        status="rate_limited",
        cycle={**empty_cycle(34), "attempted": 1, "failed": 1, "rate_limited": True},
        next_cycle_at=blocked_until,
        blocked_until=blocked_until,
        rate_limited_streak=3,
        last_error={"kind": "rate_limited", "message": "HTTP 432"},
    )

    reloaded = HealthStore(path)

    assert reloaded.snapshot["status"] == "rate_limited"
    assert reloaded.rate_limited_streak == 3
    assert reloaded.blocked_until == blocked_until.replace(microsecond=0)
    assert path.stat().st_mode & 0o777 == 0o600


def test_read_only_health_never_creates_file(tmp_path):
    path = tmp_path / "health.json"
    store = HealthStore(path, read_only=True)
    store.mark_starting(34)

    assert not path.exists()


