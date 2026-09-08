# ConnectX API notes

The ConnectX service publishes no API. These notes record what the web
dashboard at luminousconnectx.com does, established by reading its bundle and
confirming each call against a live residential account, so that the behaviour
of this integration can be checked without repeating the work.

Everything here is observation, not documentation. Any of it can change without
notice.

## Hosts

| Purpose | Base URL |
| --- | --- |
| Accounts | `https://lumconnectprodusermanagement.azurewebsites.net/v1/` |
| Plants | `https://lumconnectprodproductmanagement.azurewebsites.net/v1/` |

Two further hosts exist for the installer portal and are not used here.

## Authentication

`POST {user}/users/nox/loginEmail` with `{"email": ..., "password": ...}`.

A successful login answers 200 with the account in the body and an opaque token
in the **`Authorization` response header**, not in the body. The token is not a
JWT and carries no readable expiry.

```json
{"name": "...", "active": true, "id": "<uuid>", "user_type": 1}
```

Every subsequent request carries two headers:

```
authorization: <token exactly as returned>
X-UserID: <account id>
```

A rejected or expired token answers `401` with
`{"status": 401, "data": null, "message": "User Unauthorized"}`. The integration
treats that as a signal to log in again once and replay the request.

The login endpoint intermittently answers `500` for valid credentials and
succeeds on retry, so server errors are retried with a short backoff before
being reported.

## Response envelope

Successful calls wrap their payload:

```json
{"status": 200, "data": {...}, "message": "..."}
```

`data` is `null` when a plant does not support that reading, usually with a
descriptive message such as `No Data Found`.

## Endpoints in use

| Call | Notes |
| --- | --- |
| `GET {product}plants/getAllV1/{userId}` | `data.primaryPlant` and `data.secondaryPlant` arrays |
| `GET {product}plants/{plantId}/statsV1` | The live dashboard reading, polled every five minutes |
| `GET {product}plants/getPlantV1/{plantId}` | Site record, including the site `timeZone` |
| `GET {product}plants/getBattery/{plantId}` | Bank chemistry, capacity and pack count |
| `GET {product}plants/getInverter/{plantId}` | Model, rating and serial |
| `GET {product}plants/getSolarInfo/{plantId}` | Array configuration |
| `GET {product}plants/getDataLogger/{plantId}` | Logger serial and firmware |
| `GET {product}plants/{plantId}/power-generations?date=<epoch>` | Day series plus peak and average |
| `GET {product}plants/{plantId}/home-consumption?date=<epoch>` | Day series plus peak and average |
| `GET {user}users/system-alert-plant?plantId=&userId=` | Recent alerts, newest first |

### The date parameter

The trend endpoints reject an ISO date with `400`. They expect a Unix timestamp
in seconds for **local midnight at the site**, which is what the dashboard sends
via its `getStartDayTimeStampFromDate` helper.

## Endpoints deliberately not used

| Call | Why |
| --- | --- |
| `plants/{id}/getDetailedInfo` | Answers 500 on a residential plant |
| `plants/{id}/stringDetails` | Returns an empty `strings` array for a PCU hybrid |
| `plants/{id}/stringDetailsTrends` | Answers 204 |
| `largePlants/*` | Commercial sites only, `No Data Found` on residential |
| `plants/{id}/downloadReportV1` | Produces a spreadsheet, not a reading |
| `util/getFAQ`, `util/getExplore`, `util/getVideoGuide` | Marketing content |
| `plants/delete/{id}`, `plants/activate/{id}`, `plants/{id}/remove_user/{id}` | Destructive, and outside the scope of monitoring |
| `users/{id}/password`, `users/{id}/passwordV1`, `users/forgot-password-new-v1` | Account management |

## statsV1 fields

Readings sit in a nested `stats` object using snake case, while derived and
formatted values sit at the top level in camel case. Numbers arrive as strings,
sometimes with a unit appended, and a missing value is spelled `"null kWh"`.

### Nested `stats`

| Field | Unit | Notes |
| --- | --- | --- |
| `solar_power` | kW | Published as watts |
| `pvCurrent` | A | |
| `consumptionValue` | kW | Present home load |
| `input_voltage` | V | Grid voltage |
| `gridCTCurrent` | A | Grid current transformer, unsigned |
| `inverterCurrent` | A | Inverter output current |
| `charging_current` | A | Battery charge current |
| `discharge` | A | Battery discharge current |
| `generation` | kWh | Generation so far today |
| `battery_charge_percentage` | % | The inverter's own guess, see below |
| `grid_state` | flag | `1` mains present, `0` absent |
| `solar_state` | flag | `1` producing |
| `available_backup` | `H:MM` | |
| `time_remaining_for_charging` | `H:MM` | |
| `wifi_signal_strength` | 0-4 | |
| `last_updated_timestamp` | epoch | UTC seconds |
| `lifetimeGeneration` | 0.1 kWh | Ten times the kWh figure; the top level string is used instead |

### Top level

`batteryVoltage`, `outputVoltage`, `solarVoltage` in volts.
`lifetimeGeneration`, `todayConsumption`, `lifetimeConsumption` as strings with
a unit. `co2EmissionSaved`, `coalNotBurned`, `equivalentTreesPlanted`,
`solarPecentInConsumption`, `inverterModel`, `operatingMode`, `animationFlow`.

### Fields left alone

`consumption` tracks `inverterCurrent` multiplied by one hundred rather than any
consumption figure, so it is ignored. `feed_in` has no confirmed meaning.
`current_running_load_percentage` reports `0.00` on this hardware even under
load.

## Energy flow codes

`animationFlow` is a string such as `"4.12"` that encodes which way energy is
moving. The dashboard maps roughly ninety codes onto six directions: solar to
centre, centre to home, centre to battery, battery to centre, grid to centre and
centre to grid. That table is transcribed in `const.py` and drives the binary
sensors, and it supplies the sign for grid power, which the API reports only as
an unsigned current.

## Offline detection

The dashboard treats a plant as offline when `last_updated_timestamp` is more
than one hour old, regardless of the `online` flag in the plant list. This
integration applies the same rule.

## Battery percentage

`battery_charge_percentage` comes from the inverter, which has no connection to
the battery management system. On the reference system it reads a constant 100%
while pack voltage moves across its usable range, so it is exposed only as a
diagnostic and the integration derives its own estimate from voltage instead.
