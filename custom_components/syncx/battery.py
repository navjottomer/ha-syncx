"""Estimate battery state of charge from terminal voltage.

The inverter has no link to the battery's own management system, so the
percentage it reports is a coarse guess that spends most of its time pinned at
100%. This module derives a more honest figure from the one signal that does
track the pack: its terminal voltage.

Three corrections are applied, in order:

1. Current compensation. Terminal voltage sags under load and lifts while
   charging. Subtracting ``I * R`` recovers an approximation of the rested open
   circuit voltage, which is what the lookup curve is defined against.
2. Curve lookup. LiFePO4 has a famously flat discharge curve, so a straight
   line between empty and full volts is wrong through most of the usable range.
   A piecewise table with linear interpolation between breakpoints tracks the
   knees at each end far better.
3. Smoothing. An exponential moving average damps the step changes that follow
   large load transients, and the result is held monotonic within a charge or
   discharge leg so the figure does not jitter backwards while charging.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from itertools import pairwise

_LOGGER = logging.getLogger(__name__)

# Ignore a stored estimate older than this and start the average fresh.
MAX_ESTIMATE_AGE_SECONDS = 3600

# Below this current the pack counts as resting, and the estimate is allowed to
# move in either direction without hysteresis.
RESTING_CURRENT_A = 1.0


def parse_curve(raw: str) -> list[tuple[float, float]]:
    """Parse a ``volts:percent`` curve into descending breakpoints.

    Accepts comma or newline separated pairs, for example
    ``"3.65:100, 3.30:70, 2.90:0"``. Raises ValueError on malformed input so the
    options flow can reject it before it is stored.
    """
    points: list[tuple[float, float]] = []
    for chunk in raw.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"Expected 'volts:percent' but got {chunk!r}")
        volts_text, percent_text = chunk.split(":", 1)
        try:
            volts = float(volts_text.strip())
            percent = float(percent_text.strip())
        except ValueError as err:
            raise ValueError(f"Could not read numbers from {chunk!r}") from err
        if not 0.0 <= percent <= 100.0:
            raise ValueError(f"Percentage out of range in {chunk!r}")
        if volts <= 0.0:
            raise ValueError(f"Voltage must be positive in {chunk!r}")
        points.append((volts, percent))

    if len(points) < 2:
        raise ValueError("At least two curve points are required")

    points.sort(key=lambda point: point[0], reverse=True)

    voltages = [point[0] for point in points]
    if len(set(voltages)) != len(voltages):
        raise ValueError("Curve contains duplicate voltages")

    return points


def interpolate(curve: list[tuple[float, float]], cell_volts: float) -> float:
    """Return the percentage for one cell voltage against a descending curve."""
    if cell_volts >= curve[0][0]:
        return curve[0][1]
    if cell_volts <= curve[-1][0]:
        return curve[-1][1]

    for (high_v, high_pct), (low_v, low_pct) in pairwise(curve):
        if low_v <= cell_volts <= high_v:
            span = high_v - low_v
            if span <= 0:
                return low_pct
            ratio = (cell_volts - low_v) / span
            return low_pct + ratio * (high_pct - low_pct)

    return curve[-1][1]


@dataclass
class SocEstimatorConfig:
    """Tunables for the estimator, all surfaced in the options flow."""

    cells: int
    resistance: float
    smoothing: float
    curve: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class SocResult:
    """One estimate together with the intermediate values behind it."""

    percent: float
    rested_pack_volts: float
    cell_volts: float
    raw_percent: float
    net_current: float


class SocEstimator:
    """Stateful voltage to state-of-charge estimator for a single pack."""

    def __init__(self, config: SocEstimatorConfig) -> None:
        """Initialise with the supplied tunables."""
        self._config = config
        self._value: float | None = None
        self._last_timestamp: float | None = None

    @property
    def config(self) -> SocEstimatorConfig:
        """Return the tunables currently in force."""
        return self._config

    def reconfigure(self, config: SocEstimatorConfig) -> None:
        """Swap in new tunables and discard the running average."""
        self._config = config
        self._value = None
        self._last_timestamp = None

    def estimate(
        self,
        pack_volts: float,
        charging_current: float,
        discharging_current: float,
        timestamp: float,
    ) -> SocResult | None:
        """Return an estimate for one sample, or None if the input is unusable.

        ``charging_current`` and ``discharging_current`` are both reported as
        positive magnitudes by the API; the net is positive while charging.
        """
        config = self._config
        if pack_volts <= 0 or config.cells <= 0 or not config.curve:
            return None

        net_current = charging_current - discharging_current

        # Charging lifts the terminal voltage above the rested value and load
        # drags it below, so removing the IR term moves both cases toward the
        # open circuit voltage the curve is expressed in.
        rested_pack = pack_volts - net_current * config.resistance
        cell_volts = rested_pack / config.cells
        raw_percent = interpolate(config.curve, cell_volts)

        if (
            self._value is None
            or self._last_timestamp is None
            or timestamp - self._last_timestamp > MAX_ESTIMATE_AGE_SECONDS
        ):
            value = raw_percent
        else:
            alpha = min(max(config.smoothing, 0.01), 1.0)
            value = self._value + alpha * (raw_percent - self._value)

            # Hold the figure monotonic within a charge or discharge leg. A pack
            # that is actively charging should never be shown losing charge on
            # the strength of a momentary voltage dip, and vice versa.
            if net_current > RESTING_CURRENT_A:
                value = max(value, self._value)
            elif net_current < -RESTING_CURRENT_A:
                value = min(value, self._value)

        value = min(max(value, 0.0), 100.0)
        self._value = value
        self._last_timestamp = timestamp

        return SocResult(
            percent=round(value, 1),
            rested_pack_volts=round(rested_pack, 2),
            cell_volts=round(cell_volts, 4),
            raw_percent=round(raw_percent, 1),
            net_current=round(net_current, 2),
        )
