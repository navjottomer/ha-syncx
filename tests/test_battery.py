"""Tests for the voltage based state of charge estimator."""

from __future__ import annotations

import pytest

from custom_components.syncx.battery import (
    SocEstimator,
    SocEstimatorConfig,
    interpolate,
    parse_curve,
)
from custom_components.syncx.const import DEFAULT_SOC_CURVE


def make_estimator(smoothing: float = 1.0, resistance: float = 0.02) -> SocEstimator:
    """Return an estimator configured for a 16S LiFePO4 pack."""
    return SocEstimator(
        SocEstimatorConfig(
            cells=16,
            resistance=resistance,
            smoothing=smoothing,
            curve=parse_curve(DEFAULT_SOC_CURVE),
        )
    )


def test_parse_curve_sorts_descending() -> None:
    """Points are returned highest voltage first regardless of input order."""
    curve = parse_curve("2.90:0, 3.65:100, 3.30:70")
    assert curve == [(3.65, 100.0), (3.30, 70.0), (2.90, 0.0)]


@pytest.mark.parametrize(
    "raw",
    ["", "3.65", "a:b", "3.65:100,3.65:90", "3.65:150,2.9:0", "-1:50,2.9:0"],
)
def test_parse_curve_rejects_bad_input(raw: str) -> None:
    """Malformed curves raise rather than silently producing nonsense."""
    with pytest.raises(ValueError):
        parse_curve(raw)


def test_interpolate_clamps_outside_the_curve() -> None:
    """Voltages beyond either end return the endpoint percentage."""
    curve = parse_curve(DEFAULT_SOC_CURVE)
    assert interpolate(curve, 4.0) == 100.0
    assert interpolate(curve, 2.0) == 0.0


def test_interpolate_is_linear_between_points() -> None:
    """A voltage midway between breakpoints returns the midpoint percentage."""
    curve = parse_curve("3.40:100, 3.20:0")
    assert interpolate(curve, 3.30) == pytest.approx(50.0)


def test_resting_pack_reads_off_the_curve() -> None:
    """With no current the terminal voltage is used unmodified."""
    result = make_estimator().estimate(52.8, 0.0, 0.0, 1000.0)
    assert result is not None
    assert result.cell_volts == pytest.approx(3.30, abs=1e-4)
    assert result.percent == pytest.approx(70.0, abs=0.1)


def test_charging_current_is_compensated_downward() -> None:
    """Charging lifts terminal voltage, so the estimate must sit below it."""
    charging = make_estimator().estimate(53.0, 20.0, 0.0, 1000.0)
    resting = make_estimator().estimate(53.0, 0.0, 0.0, 1000.0)
    assert charging is not None and resting is not None
    assert charging.rested_pack_volts < resting.rested_pack_volts
    assert charging.percent < resting.percent


def test_discharging_current_is_compensated_upward() -> None:
    """Load drags terminal voltage down, so the estimate must sit above it."""
    loaded = make_estimator().estimate(53.0, 0.0, 20.0, 1000.0)
    resting = make_estimator().estimate(53.0, 0.0, 0.0, 1000.0)
    assert loaded is not None and resting is not None
    assert loaded.rested_pack_volts > resting.rested_pack_volts
    assert loaded.percent > resting.percent


def test_estimate_never_rises_while_discharging() -> None:
    """A voltage rebound under load must not report a charge increase."""
    estimator = make_estimator(smoothing=1.0)
    first = estimator.estimate(52.6, 0.0, 15.0, 1000.0)
    second = estimator.estimate(53.2, 0.0, 15.0, 1300.0)
    assert first is not None and second is not None
    assert second.percent <= first.percent


def test_estimate_never_falls_while_charging() -> None:
    """A momentary sag while charging must not report a charge decrease."""
    estimator = make_estimator(smoothing=1.0)
    first = estimator.estimate(53.2, 15.0, 0.0, 1000.0)
    second = estimator.estimate(52.6, 15.0, 0.0, 1300.0)
    assert first is not None and second is not None
    assert second.percent >= first.percent


def test_smoothing_damps_a_step_change() -> None:
    """A large jump moves the reported value only part of the way."""
    estimator = make_estimator(smoothing=0.25)
    first = estimator.estimate(52.0, 0.0, 0.0, 1000.0)
    second = estimator.estimate(53.6, 0.0, 0.0, 1300.0)
    assert first is not None and second is not None
    assert first.percent < second.percent < second.raw_percent


def test_stale_state_restarts_the_average() -> None:
    """After a long gap the estimate snaps to the fresh reading."""
    estimator = make_estimator(smoothing=0.25)
    estimator.estimate(52.0, 0.0, 0.0, 1000.0)
    result = estimator.estimate(53.6, 0.0, 0.0, 1000.0 + 7200)
    assert result is not None
    assert result.percent == result.raw_percent


def test_result_is_clamped_to_percentage_range() -> None:
    """Extreme voltages stay inside 0 to 100."""
    high = make_estimator().estimate(60.0, 0.0, 0.0, 1000.0)
    low = make_estimator().estimate(40.0, 0.0, 0.0, 1000.0)
    assert high is not None and low is not None
    assert high.percent == 100.0
    assert low.percent == 0.0


def test_unusable_voltage_returns_none() -> None:
    """A zero pack voltage produces no estimate at all."""
    assert make_estimator().estimate(0.0, 0.0, 0.0, 1000.0) is None
