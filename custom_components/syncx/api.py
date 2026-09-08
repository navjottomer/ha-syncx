"""Client for the Luminous ConnectX cloud API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from aiohttp import ClientError, ClientResponseError

from .const import BROWSER_HEADERS, PRODUCT_BASE_URL, USER_BASE_URL

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=45)

# The login endpoint intermittently answers 5xx even for valid credentials, so
# server errors are retried with a short backoff before being surfaced.
SERVER_ERROR_ATTEMPTS = 3
SERVER_ERROR_BACKOFF = 2.0


class SyncXError(Exception):
    """Base error for this integration."""


class SyncXAuthError(SyncXError):
    """Raised when the service rejects the stored credentials."""


class SyncXConnectionError(SyncXError):
    """Raised when the service could not be reached or answered unusably."""


class SyncXApiClient:
    """Talks to the ConnectX cloud on behalf of one account.

    A successful login returns an opaque token in the ``Authorization``
    response header, which is then echoed back on every subsequent request
    alongside the account's user id. Tokens expire without warning, so any
    request that comes back unauthorized triggers a single silent re-login and
    replay of the original call.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
    ) -> None:
        """Initialise the client."""
        self._session = session
        self._email = email
        self._password = password
        self._token: str | None = None
        self._user_id: str | None = None
        self._name: str | None = None
        self._login_lock = asyncio.Lock()

    @property
    def user_id(self) -> str | None:
        """Return the account id discovered at login."""
        return self._user_id

    @property
    def account_name(self) -> str | None:
        """Return the display name of the logged in account."""
        return self._name

    def update_password(self, password: str) -> None:
        """Replace the stored password and drop the current token."""
        self._password = password
        self._token = None

    async def async_login(self) -> None:
        """Authenticate and cache the resulting token and user id."""
        async with self._login_lock:
            payload = {"email": self._email, "password": self._password}
            status, headers, body = await self._raw_request(
                "POST",
                f"{USER_BASE_URL}users/nox/loginEmail",
                json=payload,
            )

            if status in (400, 401, 403):
                raise SyncXAuthError("Username or password is incorrect")
            if status != 200 or not isinstance(body, dict):
                raise SyncXConnectionError(f"Unexpected login response ({status})")

            token = headers.get("Authorization") or headers.get("authorization")
            if not token:
                raise SyncXConnectionError("Login succeeded but returned no token")

            self._token = token
            self._user_id = body.get("id")
            self._name = body.get("name")

            if not self._user_id:
                raise SyncXConnectionError("Login succeeded but returned no user id")

    async def _raw_request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], Any]:
        """Perform one HTTP call, retrying transient server errors.

        Returns the status, response headers and decoded body without applying
        any authentication logic.
        """
        request_headers = dict(BROWSER_HEADERS)
        if headers:
            request_headers.update(headers)

        last_error: Exception | None = None
        for attempt in range(SERVER_ERROR_ATTEMPTS):
            try:
                async with self._session.request(
                    method,
                    url,
                    json=json,
                    headers=request_headers,
                    timeout=REQUEST_TIMEOUT,
                ) as response:
                    text = await response.text()
                    if response.status >= 500 and attempt < SERVER_ERROR_ATTEMPTS - 1:
                        _LOGGER.debug(
                            "Server error %s from %s, retrying", response.status, url
                        )
                        await asyncio.sleep(SERVER_ERROR_BACKOFF * (attempt + 1))
                        continue

                    body: Any = text
                    if text:
                        try:
                            body = await response.json(content_type=None)
                        except (ValueError, aiohttp.ContentTypeError):
                            body = text

                    return response.status, dict(response.headers), body
            except (ClientError, ClientResponseError, TimeoutError) as err:
                last_error = err
                if attempt < SERVER_ERROR_ATTEMPTS - 1:
                    await asyncio.sleep(SERVER_ERROR_BACKOFF * (attempt + 1))
                    continue

        raise SyncXConnectionError(
            f"Could not reach {url}: {last_error}"
        ) from last_error

    async def _authenticated_get(self, url: str) -> Any:
        """GET an authenticated endpoint, re-logging in once on rejection."""
        if not self._token or not self._user_id:
            await self.async_login()

        for attempt in range(2):
            status, _headers, body = await self._raw_request(
                "GET",
                url,
                headers={
                    "authorization": self._token or "",
                    "X-UserID": self._user_id or "",
                },
            )

            # The service reports an expired token as HTTP 401, and in some
            # cases as a 200 whose envelope carries the 401 instead.
            envelope_status = body.get("status") if isinstance(body, dict) else None
            if status == 401 or envelope_status == 401:
                if attempt == 0:
                    _LOGGER.debug("Token rejected for %s, re-authenticating", url)
                    self._token = None
                    await self.async_login()
                    continue
                raise SyncXAuthError("Credentials rejected after re-authentication")

            if status != 200:
                message = (
                    body.get("message") if isinstance(body, dict) else str(body)[:200]
                )
                raise SyncXConnectionError(f"{url} returned {status}: {message}")

            if not isinstance(body, dict):
                raise SyncXConnectionError(f"{url} returned a non-JSON body")

            return body.get("data")

        raise SyncXConnectionError(f"Gave up on {url}")

    async def async_get_plants(self) -> list[dict[str, Any]]:
        """Return every plant visible to the account, primary and shared."""
        if not self._user_id:
            await self.async_login()

        data = await self._authenticated_get(
            f"{PRODUCT_BASE_URL}plants/getAllV1/{self._user_id}"
        )
        if not isinstance(data, dict):
            return []

        plants: list[dict[str, Any]] = []
        for key in ("primaryPlant", "secondaryPlant"):
            entries = data.get(key) or []
            if isinstance(entries, list):
                plants.extend(p for p in entries if isinstance(p, dict))
        return [p for p in plants if not p.get("deleted")]

    async def async_get_stats(self, plant_id: str) -> dict[str, Any]:
        """Return the live dashboard statistics for one plant."""
        data = await self._authenticated_get(
            f"{PRODUCT_BASE_URL}plants/{plant_id}/statsV1"
        )
        return data if isinstance(data, dict) else {}

    async def async_get_battery(self, plant_id: str) -> dict[str, Any]:
        """Return the configured battery bank details."""
        data = await self._authenticated_get(
            f"{PRODUCT_BASE_URL}plants/getBattery/{plant_id}"
        )
        return data if isinstance(data, dict) else {}

    async def async_get_inverter(self, plant_id: str) -> dict[str, Any]:
        """Return the inverter model and rating."""
        data = await self._authenticated_get(
            f"{PRODUCT_BASE_URL}plants/getInverter/{plant_id}"
        )
        return data if isinstance(data, dict) else {}

    async def async_get_solar(self, plant_id: str) -> dict[str, Any]:
        """Return the solar array configuration."""
        data = await self._authenticated_get(
            f"{PRODUCT_BASE_URL}plants/getSolarInfo/{plant_id}"
        )
        return data if isinstance(data, dict) else {}

    async def async_get_data_logger(self, plant_id: str) -> dict[str, Any]:
        """Return the data logger identity and firmware."""
        data = await self._authenticated_get(
            f"{PRODUCT_BASE_URL}plants/getDataLogger/{plant_id}"
        )
        return data if isinstance(data, dict) else {}

    async def async_get_generation_trend(
        self, plant_id: str, day_start: int
    ) -> dict[str, Any]:
        """Return the generation series and daily aggregates for one day.

        ``day_start`` is a Unix timestamp for local midnight at the site, which
        is the form the web dashboard sends.
        """
        data = await self._authenticated_get(
            f"{PRODUCT_BASE_URL}plants/{plant_id}/power-generations?date={day_start}"
        )
        return data if isinstance(data, dict) else {}

    async def async_get_consumption_trend(
        self, plant_id: str, day_start: int
    ) -> dict[str, Any]:
        """Return the consumption series and daily aggregates for one day."""
        data = await self._authenticated_get(
            f"{PRODUCT_BASE_URL}plants/{plant_id}/home-consumption?date={day_start}"
        )
        return data if isinstance(data, dict) else {}

    async def async_get_alerts(self, plant_id: str) -> list[dict[str, Any]]:
        """Return recent system alerts for one plant, newest first."""
        if not self._user_id:
            await self.async_login()

        data = await self._authenticated_get(
            f"{USER_BASE_URL}users/system-alert-plant"
            f"?plantId={plant_id}&userId={self._user_id}"
        )
        return data if isinstance(data, list) else []

    async def async_get_plant(self, plant_id: str) -> dict[str, Any]:
        """Return the site record for one plant."""
        data = await self._authenticated_get(
            f"{PRODUCT_BASE_URL}plants/getPlantV1/{plant_id}"
        )
        return data if isinstance(data, dict) else {}
