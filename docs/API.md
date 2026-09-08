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

## Write endpoints

The integration issues GET requests only and has no write path at all. These are
recorded because they are not discoverable from the web app, which never calls
them, and because two of them are destructive.

They were found by sending a GET to a candidate path: a route that exists but
takes a different verb answers `405` with an `Allow` header naming it, while a
route that does not exist answers `404`. Nothing needs to be written to map the
surface.

| Endpoint | Verb | Changes |
| --- | --- | --- |
| `plants/updatePlant/{plantId}` | PUT | the site record: name, location, timezone, currency |
| `plants/update/{plantId}` | PUT | same family |
| `plants/updateBattery/{plantId}` | PUT | bank brand, chemistry, capacity, pack count |
| `plants/updateSolarInfo/{plantId}` | PUT | array capacity, tilt, orientation, panel details |
| `plants/updateInverter/{plantId}` | PUT | model, rating, serial |
| `plants/activate/{plantId}` | PUT | marks the plant active |
| `plants/{plantId}/addSecondaryUser` | POST | shares the plant with another account |
| `plants/{plantId}/remove_user/{userId}` | DELETE | removes a shared user |
| `plants/delete/{plantId}` | DELETE | **deletes the plant** |
| `users/{userId}/password`, `users/{userId}/passwordV1` | PUT | account password |

`plants/updateDataLogger/{plantId}` answers 404; the logger record is not
editable this way.

Note what is absent. Nothing here writes to the inverter: there is no operating
mode, charge current, voltage limit, export cap or schedule. These endpoints
edit the *record* of the installation, not the installation. The data logger
pushes to the cloud and the cloud never pushes back, which is also why the five
minute sample rate cannot be improved from this side.

### These PUTs null fields they do not recognise

`updateSolarInfo` was sent the exact body that `getSolarInfo` had just returned,
with one string changed. The server accepted it, and silently set
`noofPanelsinSeries` and `noofPanelsInParallel` to null. Recovering them needed
the same values sent again under several spellings, one of which the server
accepted.

So the GET and PUT shapes are not symmetric, and a round trip is not safe.
**Read the record first, keep a copy, write, then read back and compare field by
field.** Do not assume that echoing the response preserves it.

### PM Surya Ghar and the MNRE flag

`statsV1` and `getPlantV1` both return `mnreStatus` and `checkMnre`, which relate
to the Indian rooftop subsidy scheme. `checkMnre` means "already asked", so once
it is true the app stops offering the question; `mnreStatus` is the answer.

The web app cannot set it, and it is not a field on `updatePlant`: the Android
app's `UpdatePlantInfoDetails` request model, recovered by decompiling
`com.luminous.connectx`, has no such field. It is a separate endpoint, taking
query parameters rather than a body:

```
POST v1/plants/saveMnreStatus?mnreStatus={true|false}&plantId={plantId}
Headers: X-UserID, Authorization
(empty body)
```

`GET` against it answers `405 Allow: POST`, and a successful `POST` returns
`{"status":200,"data":null,"message":"MNRE status updated successfully"}`. This
is the only way to revise the answer once `checkMnre` is set, since the app no
longer shows the prompt.

## Endpoints deliberately not used

| Call | Why |
| --- | --- |
| `plants/{id}/getDetailedInfo` | Answers 500 on a residential plant |
| `plants/{id}/stringDetails` | Returns an empty `strings` array for a PCU hybrid |
| `plants/{id}/stringDetailsTrends` | Answers 204 |
| `largePlants/*` | Commercial sites only, `No Data Found` on residential |
| `plants/{id}/downloadReportV1` | Produces a spreadsheet, not a reading |
| `util/getFAQ`, `util/getExplore`, `util/getVideoGuide` | Marketing content |
| `plants/delete/{id}`, `plants/activate/{id}`, `plants/{id}/remove_user/{id}` | Destructive, and outside the scope of monitoring; see the write endpoints above |
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
| `consumptionValue` | kW | Present home load, see the power factor note |
| `input_voltage` | V | Grid voltage |
| `gridCTCurrent` | A | Grid current transformer, unsigned, see below |
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

## The load power factor

`consumptionValue` is not an independent measurement. It is `inverterCurrent`
times `outputVoltage` times a constant, measured across consecutive samples as:

| load | output A | output V | VA | ratio |
| --- | --- | --- | --- | --- |
| 720 | 3.43 | 262.3 | 900 | 0.800 |
| 520 | 2.61 | 247.6 | 646 | 0.805 |
| 500 | 2.39 | 259.3 | 620 | 0.807 |
| 790 | 3.93 | 251.6 | 989 | 0.799 |
| 1010 | 5.04 | 249.4 | 1257 | 0.804 |
| 970 | 4.88 | 249.2 | 1216 | 0.798 |

Mean 0.8024, standard deviation 0.0032. That is an assumed power factor, not a
measured one, so the published load is only as good as that assumption. This
integration computes the load from the same two primaries with the factor
exposed as a setting, which reproduces the vendor figure exactly at 0.8.

Note also that `solar_power` equals `pvCurrent` times `solarVoltage` exactly, so
solar is a DC measurement while the load is AC.

## The grid current transformer

`gridCTCurrent` does not reconcile with the rest of the sample. Across eight
consecutive polls on the reference system it read between 200 W and 1200 W above
`solar_power - consumptionValue - battery`, with no constant offset or scale
that would explain it:

| solar | load | battery | balance | CT | difference |
| --- | --- | --- | --- | --- | --- |
| 3277 | 720 | 120 | 2437 | 3062 | +625 |
| 446 | 520 | 0 | -74 | 804 | +878 |
| 3169 | 500 | 0 | 2669 | 3037 | +368 |
| 2154 | 770 | 134 | 1250 | 2428 | +1178 |
| 3078 | 930 | 133 | 2015 | 2215 | +200 |
| 1699 | 970 | -78 | 807 | 1691 | +884 |

Nor can it be the inverter's grid port. On the last row, treating its 1691 W as
export implies a house load of 86 W, and as import implies 3468 W, against the
970 W the inverter reports for the same instant. Current based forms fare no
better: `(gridCTCurrent - inverterCurrent) x voltage` averages 189 W out but is
951 W out at worst.

The likely explanation is that the clamp sits on the incoming mains and measures
the whole house rather than the inverter's grid connection, which would also
explain an air conditioner reporting more energy than the inverter ever measured
for the entire property.

So grid power is derived from the balance instead, and the current transformer
is published only as the raw current and voltage it actually is.

## Energy flow codes

`animationFlow` is a string such as `"4.12"` that encodes which way energy is
moving. The dashboard maps roughly ninety codes onto six directions: solar to
centre, centre to home, centre to battery, battery to centre, grid to centre and
centre to grid. That table is transcribed in `const.py` and drives the solar and
battery binary sensors.

It does not decide the grid direction. The table has real gaps -- codes such as
4.10 and 4.6 fall through to no flow at all -- and on a sample where it does
resolve it can still disagree with the measured balance. Import and export
follow the balance instead, so the direction never contradicts the grid power
figure shown next to it.

## Offline detection

The dashboard treats a plant as offline when `last_updated_timestamp` is more
than one hour old, regardless of the `online` flag in the plant list. This
integration applies the same rule.

## Battery percentage

`battery_charge_percentage` comes from the inverter, which has no connection to
the battery management system. On the reference system it reads a constant 100%
while pack voltage moves across its usable range, so it is exposed only as a
diagnostic and the integration derives its own estimate from voltage instead.
