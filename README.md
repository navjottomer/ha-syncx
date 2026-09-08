# Luminous ConnectX for Home Assistant

An unofficial Home Assistant integration for rooftop solar plants monitored
through [luminousconnectx.com](https://luminousconnectx.com). It signs in with
the same email and password you use on the web dashboard, polls the plant every
five minutes, and exposes the readings as regular Home Assistant entities.

It also estimates battery state of charge from pack voltage, because the
inverter has no link to the battery's own management system and the percentage
it reports is not trustworthy.

> Not affiliated with or endorsed by Luminous. The service offers no public API,
> so this integration speaks the same private endpoints the web dashboard uses
> and may break if those change.

## Installation

### HACS

1. In HACS, open the three dot menu and choose **Custom repositories**.
2. Add `https://github.com/navjottomer/ha-syncx` with the category
   **Integration**.
3. Install **Luminous ConnectX** and restart Home Assistant.

### Manual

Copy `custom_components/syncx` into your Home Assistant `config/custom_components`
directory and restart.

## Setup

Go to **Settings → Devices & services → Add integration** and search for
**Luminous ConnectX**. Enter your account email and password. If the account has
more than one plant you will be asked which to add; add the integration again to
set up the others.

Credentials are stored in the Home Assistant config entry, the same place every
other cloud integration keeps them. When the stored password stops working, the
integration raises a repair notification and reopens the login form rather than
retrying in a loop.

## Entities

### Solar

| Entity | Unit |
| --- | --- |
| Solar power | W |
| Solar voltage | V |
| Solar current | A |
| Generation today | kWh |
| Lifetime generation | kWh |
| Solar share of consumption | % |

### Battery

| Entity | Unit |
| --- | --- |
| Battery level | % (estimated from voltage, see below) |
| Battery level reported by inverter | % (diagnostic, what the vendor app shows) |
| Battery voltage | V |
| Battery charging current | A |
| Battery discharging current | A |
| Battery power | W, positive while charging |
| Backup time remaining | min |
| Charging time remaining | min |

### Grid and load

| Entity | Unit |
| --- | --- |
| Grid voltage | V |
| Grid current | A |
| Grid power | W, negative while exporting, derived (see below) |
| Load power | W |
| Load | % |
| Output voltage | V |
| Output current | A |
| Consumption today | kWh |
| Lifetime consumption | kWh |
| Peak solar power today | W |
| Average solar power today | W |
| Peak load power today | W |
| Average load power today | W |

### Binary sensors

Online, Grid available, Solar producing, Battery charging, Battery discharging,
Exporting to grid, Importing from grid, Solar contributing.

The charging, discharging, export and import states come from the same energy
flow code the vendor dashboard uses to animate its diagram, so they agree with
what the web app shows.

### Diagnostic

CO2 saved, Coal not burned, Equivalent trees planted, Wi-Fi signal strength,
Last reading, Operating mode, Energy flow, Latest alert.

**Latest alert** carries the most recent notification from the service, such as
a mains failure or restoration, with the message, alert name and timestamp as
attributes. It is the same feed the vendor app shows under notifications.

Two entities stay unavailable on hardware that does not report them. On the
reference system the service returns the literal text `"null kWh"` for lifetime
consumption, and omits inverter frequency entirely, so **Lifetime consumption**
reads unknown and **Inverter frequency** is disabled by default.

## How grid power is worked out

The inverter publishes a grid current transformer reading, but it does not
reconcile with the solar, load and battery figures from the same sample, and it
cannot be squared with the load the inverter itself reports. It appears to clamp
the whole incoming mains rather than the inverter's grid connection.

**Grid power** is therefore computed from the inverter's own balance:

```
grid = solar x efficiency - home load - battery
```

positive while importing and negative while exporting. That keeps the picture
self consistent, so solar always equals home plus battery plus grid, and the
import and export states can never contradict the power figure beside them.

Everything in that expression comes from the inverter's primary measurements:

| Term | Built from |
| --- | --- |
| Solar | `pvCurrent` x `solarVoltage`, on the DC side |
| Battery | an external BMS sensor if configured, otherwise `batteryVoltage` x (`charging_current` - `discharge`) |
| Home load | `inverterCurrent` x `outputVoltage` x power factor, AC |

Two of those need a constant, and both are settings rather than guesses baked
into the code:

- **Inverter efficiency**, default 0.95. Solar is measured before the inverter
  and the load after it, so the DC figure is scaled to its AC equivalent before
  the subtraction. Set it to 1.0 to apply no correction.
- **Battery power sensor**, optional. The inverter's current sensing reads zero
  below a few amps. On the reference system it reported no current while the
  battery management system measured 9 A going in, which was enough to put grid
  power on the wrong side of zero. Point this at a BMS power sensor, positive
  while charging, and the balance uses that instead. It falls back to the
  inverter automatically if the sensor goes unavailable.
- **Load power factor**, default 0.8. The service itself derives its published
  load as output current times output voltage times exactly 0.8, so 0.8 keeps
  the load matching the vendor app. Raise it if you know your loads are better
  than that.

The raw figures are published too, as **Grid current**, **Grid voltage**,
**Output apparent power** and **Grid apparent power**, so nothing is hidden
behind the derivation. The evidence is in [docs/API.md](docs/API.md).

## Battery charge estimation

The inverter reports battery percentage without talking to the pack's BMS, so it
tends to sit at 100% regardless of the actual charge. This integration derives a
separate figure from pack voltage, which does track the real state.

Configure it under the integration's **Configure** button.

| Setting | Default | Meaning |
| --- | --- | --- |
| Estimate charge from voltage | on | Turn the estimate off to keep only the reported value |
| Cells in series | 16 | 16 for a 51.2 V nominal LiFePO4 bank, 15 for 48 V lead acid |
| Pack internal resistance | 0.02 Ω | Cancels sag under load and lift while charging |
| Smoothing factor | 0.25 | Lower smooths harder and reacts more slowly |
| Discharge curve | LiFePO4 | Per-cell `volts:percent` breakpoints, highest first |

Three corrections are applied to each sample:

1. **Current compensation.** Terminal voltage sags under load and lifts while
   charging, so `V_rest = V_pack − I_net × R` recovers an approximation of the
   rested open circuit voltage the curve is defined against.
2. **Curve lookup.** LiFePO4 has a very flat discharge curve, so a straight line
   from empty to full volts is badly wrong through the middle of the range. The
   default table places breakpoints at the knees and interpolates between them.
3. **Smoothing.** An exponential moving average damps the steps that follow
   large load changes, and the value is held monotonic within a charge or
   discharge leg so it never ticks backwards while the pack is charging.

The default curve targets LiFePO4:

```
3.650:100, 3.400:99, 3.330:90, 3.300:70, 3.280:50,
3.250:30, 3.200:20, 3.100:10, 2.900:0
```

For flooded lead acid, a reasonable starting point per 2 V cell is:

```
2.12:100, 2.08:75, 2.03:50, 1.98:25, 1.90:0
```

The estimate is an approximation. Voltage-based state of charge is inherently
imprecise for LiFePO4, and it is least reliable during heavy charge or discharge.
Tune the resistance first if the reading swings when a large load switches on,
and adjust the curve breakpoints against what your pack actually does over a full
cycle.

The `sensor.*_battery_level` entity exposes the intermediate values as
attributes, which is the quickest way to see why it landed where it did:

- `rested_pack_voltage` — terminal voltage after current compensation
- `cell_voltage` — rested voltage divided by the series cell count
- `unsmoothed_percent` — the raw curve lookup before smoothing
- `net_current` — charge current minus discharge current

## Energy dashboard

The integration provides everything the Home Assistant energy dashboard needs:

| Energy dashboard slot | Entity |
| --- | --- |
| Solar production | **Lifetime generation** |
| Grid consumption | **Grid imported energy** |
| Return to grid | **Grid exported energy** |
| Battery in | **Battery charged energy** |
| Battery out | **Battery discharged energy** |

**Lifetime generation** comes straight from the service. The other four do not
exist upstream: this inverter reports grid and battery only as instantaneous
power, with no energy counters behind them, so those totals are integrated here
from the power readings.

Each new sample contributes the trapezoid between the previous power and the
current one. The totals are restored across restarts, and a gap longer than an
hour is skipped rather than filled in, so an outage does not invent energy that
was never measured.

Accuracy is bounded by the five minute sampling rate. Steady loads integrate
well; a short spike between two polls is not seen at all. Treat these as a good
approximation rather than a revenue meter, and prefer the service's own
**Lifetime generation** wherever it covers what you need.

**Home consumed energy** is provided on the same basis, since the service
returns no lifetime consumption figure on this hardware.

## Polling

The data logger uploads a fresh sample roughly every five minutes, so the
integration polls at that interval. Each cycle makes four calls: the live
reading, the two daily trend series, and the alert feed. Site and hardware
records change rarely and are re-read once an hour.

A failure in the trend or alert calls is logged and skipped rather than failing
the whole cycle, so the core readings survive a partial outage.

A reading older than one hour marks the plant offline, matching the rule the
vendor dashboard applies.

## Troubleshooting

Download diagnostics from the device page before opening an issue. Credentials,
serial numbers and location are redacted automatically.

To raise the log level, add to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.syncx: debug
```

## How this works

The service publishes no API, so this integration speaks the same private
endpoints the web dashboard uses, presenting the same request headers a browser
would. What is known about those endpoints, including the field units and the
quirks worth knowing, is written up in [docs/API.md](docs/API.md).

## Licence

MIT
