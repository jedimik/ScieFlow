from datetime import datetime, timedelta, timezone

import pytest

import budget


def test_new_budget_zero_spent():
    b = budget.new_budget(5, 40, 240)
    assert b["spent"] == {"iterations": 0, "experiment_runs": 0, "wall_minutes": 0}
    assert budget.low_dimensions(b) == []


def test_record_and_remaining_fraction():
    b = budget.new_budget(5, 40, 240)
    budget.record(b, iterations=1, experiment_runs=6)
    frac = budget.remaining_fraction(b)
    assert frac["iterations"] == pytest.approx(0.8)
    assert frac["experiment_runs"] == pytest.approx(34 / 40)
    with pytest.raises(ValueError):
        budget.record(b, tokens=1)


def test_low_dimensions_at_ten_percent():
    b = budget.new_budget(10, 40, 240)
    budget.record(b, iterations=9)
    assert budget.low_dimensions(b) == ["iterations"]      # exactly 10% left
    budget.record(b, iterations=1)
    assert budget.exhausted(b) == ["iterations"]


def test_wall_clock_from_started():
    b = budget.new_budget(5, 40, 60)
    start = datetime.fromisoformat(b["started"])
    budget.set_wall_from_clock(b, now=start + timedelta(minutes=57))
    assert budget.low_dimensions(b) == ["wall_minutes"]    # 3/60 = 5% left


def test_roundtrip(tmp_path):
    b = budget.new_budget(5, 40, 240)
    budget.record(b, experiment_runs=3)
    budget.write_budget(tmp_path, b)
    assert budget.read_budget(tmp_path)["spent"]["experiment_runs"] == 3


def test_record_is_all_or_nothing():
    b = budget.new_budget(5, 40, 240)
    with pytest.raises(ValueError):
        budget.record(b, iterations=2, bogus=1)
    assert b["spent"]["iterations"] == 0


def test_wall_clock_rejects_naive_datetime():
    b = budget.new_budget(5, 40, 60)
    with pytest.raises(ValueError):
        budget.set_wall_from_clock(b, now=datetime(2026, 7, 11, 12, 0, 0))
