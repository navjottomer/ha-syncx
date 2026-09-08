"""Constants for the Luminous ConnectX integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "syncx"

USER_BASE_URL: Final = "https://lumconnectprodusermanagement.azurewebsites.net/v1/"
PRODUCT_BASE_URL: Final = (
    "https://lumconnectprodproductmanagement.azurewebsites.net/v1/"
)

DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=5)

# The data logger pushes a fresh sample roughly every five minutes. The web app
# treats a sample older than one hour as the device being offline.
STALE_AFTER: Final = timedelta(hours=1)

# Slow-moving metadata (battery bank, inverter model, data logger firmware) is
# refreshed once every N polls rather than on every cycle.
DETAIL_REFRESH_EVERY: Final = 12

CONF_PLANT_ID: Final = "plant_id"
CONF_PLANT_NAME: Final = "plant_name"
CONF_USER_ID: Final = "user_id"

# Battery state-of-charge estimation options.
CONF_SOC_ENABLED: Final = "soc_enabled"
CONF_SOC_CELLS: Final = "soc_cells"
CONF_SOC_RESISTANCE: Final = "soc_resistance"
CONF_SOC_CURVE: Final = "soc_curve"
CONF_SOC_SMOOTHING: Final = "soc_smoothing"

DEFAULT_SOC_ENABLED: Final = True

# Series cell count. A 51.2 V nominal LiFePO4 pack is 16 cells of 3.2 V.
DEFAULT_SOC_CELLS: Final = 16

# Effective pack internal resistance in ohms, used to back out the sag or lift
# that load and charge current impose on the terminal voltage. A 16S 100 Ah
# LiFePO4 bank sits near 20 mOhm once cell resistance, busbars and cabling are
# taken together.
DEFAULT_SOC_RESISTANCE: Final = 0.02

# Exponential moving average weight applied to each new estimate. Lower values
# smooth harder at the cost of responsiveness.
DEFAULT_SOC_SMOOTHING: Final = 0.25

# Per-cell open circuit voltage to state-of-charge breakpoints for LiFePO4,
# highest voltage first. Values in between are linearly interpolated.
DEFAULT_SOC_CURVE: Final = (
    "3.650:100, 3.400:99, 3.330:90, 3.300:70, 3.280:50, "
    "3.250:30, 3.200:20, 3.100:10, 2.900:0"
)

# Browser-equivalent request headers. The upstream service is only ever spoken
# to by its own Angular single page app, so the client presents the same shape
# of request that app does.
BROWSER_HEADERS: Final[dict[str, str]] = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://luminousconnectx.com",
    "Referer": "https://luminousconnectx.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
    "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
}

# Grid flow below this many watts counts as neither import nor export, which
# keeps the direction from flapping on measurement noise.
GRID_DEADBAND_W: Final = 100.0

# Energy flow directions the dashboard derives from the animationFlow code.
FLOW_SOLAR_TO_CENTER: Final = "solar_to_center"
FLOW_CENTER_TO_HOME: Final = "center_to_home"
FLOW_CENTER_TO_BATTERY: Final = "center_to_battery"
FLOW_BATTERY_TO_CENTER: Final = "battery_to_center"
FLOW_GRID_TO_CENTER: Final = "grid_to_center"
FLOW_CENTER_TO_GRID: Final = "center_to_grid"

ALL_FLOWS: Final = (
    FLOW_SOLAR_TO_CENTER,
    FLOW_CENTER_TO_HOME,
    FLOW_CENTER_TO_BATTERY,
    FLOW_BATTERY_TO_CENTER,
    FLOW_GRID_TO_CENTER,
    FLOW_CENTER_TO_GRID,
)

# animationFlow code to the set of active flows, transcribed from the vendor
# dashboard. Codes absent from this table mean "no flow".
ANIMATION_FLOW_MAP: Final[dict[str, tuple[str, ...]]] = {
    code: flows
    for codes, flows in (
        (
            ("1.1", "2.1", "3.1", "4.1", "13.2", "10.2", "15.6"),
            (FLOW_SOLAR_TO_CENTER, FLOW_CENTER_TO_HOME),
        ),
        (
            ("1.2", "2.2", "3.2", "4.2", "13.3", "15.11"),
            (FLOW_SOLAR_TO_CENTER, FLOW_CENTER_TO_HOME, FLOW_CENTER_TO_BATTERY),
        ),
        (
            ("1.3", "2.3", "3.3", "4.3", "13.4", "15.14"),
            (FLOW_SOLAR_TO_CENTER, FLOW_CENTER_TO_HOME, FLOW_BATTERY_TO_CENTER),
        ),
        (
            ("1.4", "2.4", "3.4", "4.4", "6.2", "7.2", "13.5", "9.2", "14.2", "15.2"),
            (FLOW_BATTERY_TO_CENTER, FLOW_CENTER_TO_HOME),
        ),
        (
            (
                "1.5",
                "2.5",
                "3.5",
                "4.5",
                "6.3",
                "7.3",
                "13.6",
                "10.3",
                "9.3",
                "14.3",
                "15.3",
            ),
            (FLOW_GRID_TO_CENTER, FLOW_CENTER_TO_HOME),
        ),
        (
            ("1.7", "2.7", "3.7", "4.7", "6.4", "7.4", "13.7", "9.4", "14.4", "15.4"),
            (FLOW_GRID_TO_CENTER, FLOW_CENTER_TO_BATTERY, FLOW_CENTER_TO_HOME),
        ),
        (
            ("1.8", "2.8", "3.8", "4.8", "13.8", "15.7"),
            (FLOW_SOLAR_TO_CENTER, FLOW_CENTER_TO_BATTERY),
        ),
        (
            ("4.9", "6.5", "13.9", "9.5", "14.5", "15.5"),
            (FLOW_GRID_TO_CENTER, FLOW_CENTER_TO_BATTERY),
        ),
        (
            ("1.11", "2.11", "3.11", "4.11", "13.10", "15.13"),
            (
                FLOW_SOLAR_TO_CENTER,
                FLOW_CENTER_TO_BATTERY,
                FLOW_GRID_TO_CENTER,
                FLOW_CENTER_TO_HOME,
            ),
        ),
        (
            ("1.12", "2.12", "3.12", "13.11", "15.15"),
            (FLOW_SOLAR_TO_CENTER, FLOW_GRID_TO_CENTER, FLOW_CENTER_TO_HOME),
        ),
        (
            ("4.12", "13.12", "15.9"),
            (
                FLOW_SOLAR_TO_CENTER,
                FLOW_CENTER_TO_BATTERY,
                FLOW_CENTER_TO_GRID,
                FLOW_CENTER_TO_HOME,
            ),
        ),
        (
            ("4.13", "13.13", "15.10"),
            (FLOW_SOLAR_TO_CENTER, FLOW_CENTER_TO_BATTERY, FLOW_CENTER_TO_GRID),
        ),
        (
            ("4.14", "5.1", "13.14", "10.5", "15.8"),
            (FLOW_SOLAR_TO_CENTER, FLOW_CENTER_TO_GRID),
        ),
        (
            ("4.15", "13.15", "10.6", "15.12"),
            (FLOW_SOLAR_TO_CENTER, FLOW_CENTER_TO_HOME, FLOW_CENTER_TO_GRID),
        ),
        (
            ("13.16", "15.16"),
            (
                FLOW_SOLAR_TO_CENTER,
                FLOW_CENTER_TO_HOME,
                FLOW_CENTER_TO_GRID,
                FLOW_BATTERY_TO_CENTER,
            ),
        ),
        (
            ("10.4",),
            (FLOW_SOLAR_TO_CENTER, FLOW_CENTER_TO_HOME, FLOW_GRID_TO_CENTER),
        ),
        (("15.17",), (FLOW_BATTERY_TO_CENTER, FLOW_CENTER_TO_GRID)),
        (
            ("15.18",),
            (FLOW_BATTERY_TO_CENTER, FLOW_CENTER_TO_HOME, FLOW_CENTER_TO_GRID),
        ),
    )
    for code in codes
}
