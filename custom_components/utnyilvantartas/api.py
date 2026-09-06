from __future__ import annotations

import asyncio
import json
import time as time_module
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

from .const import BASE_URL, LOGIN_URL, PAST_URL, ROUTE_LINES_URL, ROUTE_GRID_URL

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)


class AlapnyomkovetesError(Exception):
    """Base API error."""


class AlapnyomkovetesAuthError(AlapnyomkovetesError):
    """Authentication error."""


@dataclass(slots=True)
class ApiResult:
    data: dict[str, Any]


class AlapnyomkovetesClient:
    """Small async client for the Alapnyomkövetés web endpoints."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._logged_in = False

    async def async_close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=45)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                cookie_jar=aiohttp.CookieJar(),
                headers={
                    "User-Agent": UA,
                    "Accept-Language": "hu,en;q=0.8",
                },
            )
            self._logged_in = False
        return self._session

    async def async_login(self) -> None:
        async with self._lock:
            session = await self._ensure_session()

            # Get an initial PHPSESSID / load balancer cookie.
            async with session.get(
                LOGIN_URL,
                headers={"Accept": "text/html,application/xhtml+xml"},
            ) as response:
                if response.status >= 400:
                    raise AlapnyomkovetesAuthError(
                        f"Login oldal HTTP {response.status}"
                    )
                await response.read()

            payload = {
                "username": self.username,
                "password": self.password,
                "login": "Belépés",
            }
            async with session.post(
                LOGIN_URL,
                data=payload,
                allow_redirects=True,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": BASE_URL,
                    "Referer": LOGIN_URL,
                },
            ) as response:
                text = await response.text(errors="replace")
                final_url = str(response.url)
                if response.status >= 400:
                    raise AlapnyomkovetesAuthError(
                        f"Login HTTP {response.status}"
                    )

            lower = text.lower()
            if final_url.rstrip("/").endswith("/login"):
                raise AlapnyomkovetesAuthError("Sikertelen bejelentkezés")
            if 'name="password"' in lower and 'name="username"' in lower:
                raise AlapnyomkovetesAuthError("Sikertelen bejelentkezés")

            cookie_names = {cookie.key for cookie in session.cookie_jar}
            if "PHPSESSID" not in cookie_names:
                raise AlapnyomkovetesAuthError("A szerver nem adott PHPSESSID cookie-t")

            self._logged_in = True

    async def async_fetch_past(
        self,
        device_id: int,
        dt_from: datetime,
        dt_to: datetime,
    ) -> ApiResult:
        if not self._logged_in:
            await self.async_login()

        for attempt in range(2):
            session = await self._ensure_session()
            payload = [
                ("devices[]", str(device_id)),
                ("from", dt_from.strftime("%Y-%m-%d %H:%M")),
                ("to", dt_to.strftime("%Y-%m-%d %H:%M")),
                ("colors[]", "#0000FF"),
                ("colors[]", "#FF0000"),
                ("displaymode", "false"),
                ("displaymodeSnapped", "false"),
                ("fuel", "0"),
            ]

            async with session.post(
                PAST_URL,
                data=payload,
                allow_redirects=True,
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": BASE_URL,
                    "Referer": f"{BASE_URL}/positions/past",
                    "X-Requested-With": "XMLHttpRequest",
                },
            ) as response:
                raw = await response.read()
                final_url = str(response.url)

            # Expired session normally redirects to /login or returns HTML.
            if final_url.rstrip("/").endswith("/login"):
                self._logged_in = False
                if attempt == 0:
                    await self.async_login()
                    continue
                raise AlapnyomkovetesAuthError("A munkamenet lejárt")

            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as err:
                if attempt == 0:
                    self._logged_in = False
                    await self.async_login()
                    continue
                raise AlapnyomkovetesError("A szerver válasza nem JSON") from err

            if not isinstance(data, dict):
                raise AlapnyomkovetesError("Érvénytelen API-válasz")
            if data.get("success") != 1:
                raise AlapnyomkovetesError(f"API success={data.get('success')!r}")
            return ApiResult(data=data)

        raise AlapnyomkovetesError("Sikertelen API-lekérés")

    @staticmethod
    def _decode_route_response(raw: bytes, content_type: str) -> Any:
        """Decode route statistics response without assuming its final schema yet."""
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {
                "_content_type": content_type,
                "_raw_text": text,
            }

    async def async_fetch_route_statistics(
        self,
        device_id: int,
        dt_from: datetime,
        dt_to: datetime,
    ) -> ApiResult:
        """Fetch the Alapnyomkövetés Statistics/Route data for one device/day.

        Browser flow discovered from DevTools:
          1. POST /statistics/ajaxGetLocationLines with date/filter form data.
          2. GET  /statistics/ajaxGetStatDeviceGrid/{device_id}?_=<cachebuster>

        Both requests must use the SAME authenticated session because the server
        may keep the selected route filter in PHP session state.
        """
        if not self._logged_in:
            await self.async_login()

        for attempt in range(2):
            session = await self._ensure_session()
            route_payload = [
                ("devices[]", str(device_id)),
                ("from", dt_from.strftime("%Y-%m-%d %H:%M")),
                ("to", dt_to.strftime("%Y-%m-%d %H:%M")),
                ("radio", "2"),
                ("time", "3"),
                ("distance", "50"),
                ("odometer", "0"),
                ("sepCountry", "0"),
                ("sepMidnight", "0"),
                ("sectionByDist", ""),
                ("sectionByTime", ""),
                ("colors[]", "#0000FF"),
                ("colors[]", "#FF0000"),
            ]
            common_headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": f"{BASE_URL}/statistics/route",
                "X-Requested-With": "XMLHttpRequest",
            }

            async with session.post(
                ROUTE_LINES_URL,
                data=route_payload,
                allow_redirects=True,
                headers={**common_headers, "Origin": BASE_URL},
            ) as response:
                lines_raw = await response.read()
                lines_url = str(response.url)
                lines_status = response.status
                lines_ct = response.headers.get("Content-Type", "")

            if lines_url.rstrip("/").endswith("/login") or lines_status in {401, 403}:
                self._logged_in = False
                if attempt == 0:
                    await self.async_login()
                    continue
                raise AlapnyomkovetesAuthError("A Statistics/Route munkamenet lejárt")
            if lines_status >= 400:
                raise AlapnyomkovetesError(
                    f"ajaxGetLocationLines HTTP {lines_status}"
                )

            cachebuster = int(time_module.time() * 1000)
            grid_url = f"{ROUTE_GRID_URL}/{device_id}?_={cachebuster}"
            async with session.get(
                grid_url,
                allow_redirects=True,
                headers=common_headers,
            ) as response:
                grid_raw = await response.read()
                grid_final_url = str(response.url)
                grid_status = response.status
                grid_ct = response.headers.get("Content-Type", "")

            if grid_final_url.rstrip("/").endswith("/login") or grid_status in {401, 403}:
                self._logged_in = False
                if attempt == 0:
                    await self.async_login()
                    continue
                raise AlapnyomkovetesAuthError("A Statistics/Route munkamenet lejárt")
            if grid_status >= 400:
                raise AlapnyomkovetesError(
                    f"ajaxGetStatDeviceGrid HTTP {grid_status}"
                )

            lines_data = self._decode_route_response(lines_raw, lines_ct)
            grid_data = self._decode_route_response(grid_raw, grid_ct)
            return ApiResult(
                data={
                    "success": 1,
                    "device_id": device_id,
                    "from": dt_from.strftime("%Y-%m-%d %H:%M"),
                    "to": dt_to.strftime("%Y-%m-%d %H:%M"),
                    "filters": {
                        "radio": 2,
                        "time": 3,
                        "distance": 50,
                        "odometer": 0,
                        "sepCountry": 0,
                        "sepMidnight": 0,
                    },
                    "location_lines": lines_data,
                    "device_grid": grid_data,
                    "http": {
                        "location_lines_status": lines_status,
                        "location_lines_content_type": lines_ct,
                        "device_grid_status": grid_status,
                        "device_grid_content_type": grid_ct,
                    },
                }
            )

        raise AlapnyomkovetesError("Sikertelen Statistics/Route lekérés")

