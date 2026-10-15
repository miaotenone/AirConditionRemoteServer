"""Weather-based suggestions -- the decision is pure, the fetching is not.

Thresholds are the user's own table (design Q2): outdoors 35℃ and up suggests
26℃ cooling, down through "just ventilate" to "heat". A forecast about to get
much cooler nudges the suggestion up a degree (design Q2b).

The network calls live here too but behind their own functions, so the whole
decision is unit tested from a fixed payload.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from .state import AC_MODE, TEMP_DEFAULT, clamp_temp

log = logging.getLogger(__name__)

COOL = AC_MODE["cool"]
FAN = AC_MODE["fan"]
HEAT = AC_MODE["heat"]

#: A drop of this much over the next two hours counts as a cold snap.
COLD_SNAP_DELTA = -2


@dataclass(frozen=True)
class Recommendation:
    """A suggested AC setting, plus a short line for the card."""

    mode: int
    temp: int
    fan: int
    note: str

    @property
    def temp_used(self) -> bool:
        """False for a fan-only suggestion, where the temperature is moot."""
        return self.mode != FAN


@dataclass(frozen=True)
class WeatherNow:
    """The slice of a forecast we actually use."""

    outdoor_c: int
    humidity: Optional[int] = None
    next2h_delta_c: int = 0


def recommend(outdoor_c: float, trend_delta_c: float = 0) -> Recommendation:
    """Turn an outdoor temperature into a suggested AC setting.

    Bands (design Q2): ``>=35 -> 26``, ``30..35 -> 25``, ``26..30 -> 24``,
    ``18..26 -> fan only``, ``<18 -> heat``. A coming cold snap raises the
    cooling/heating target by one degree (design Q2b).
    """
    outdoor = int(round(outdoor_c))

    if outdoor >= 35:
        mode, temp, note = COOL, 26, f"外面 {outdoor}℃，建议制冷 26℃"
    elif outdoor >= 30:
        mode, temp, note = COOL, 25, f"外面 {outdoor}℃，建议制冷 25℃"
    elif outdoor >= 26:
        mode, temp, note = COOL, 24, f"外面 {outdoor}℃，建议制冷 24℃"
    elif outdoor >= 18:
        mode, temp, note = FAN, TEMP_DEFAULT, f"外面 {outdoor}℃，凉爽，建议送风通风"
    else:
        mode, temp, note = HEAT, 20, f"外面 {outdoor}℃，建议制热 20℃"

    if mode != FAN and trend_delta_c <= COLD_SNAP_DELTA:
        temp = clamp_temp(temp + 1)
        note += "（两小时内要降温，上调 1℃）"

    return Recommendation(mode=mode, temp=temp, fan=0, note=note)


# ------------------------------------------------------------------- fetching


def parse_open_meteo(payload: dict) -> Optional[WeatherNow]:
    """Read the fields we need out of an Open-Meteo response.

    Returns ``None`` when the payload carries no current temperature -- better
    to say "没拿到天气" than to suggest a setting off a missing value.
    """
    if not isinstance(payload, dict):
        return None
    current = payload.get("current") or {}
    try:
        outdoor = int(round(float(current.get("temperature_2m"))))
    except (TypeError, ValueError):
        return None

    humidity = current.get("relative_humidity_2m")
    try:
        humidity = int(humidity) if humidity is not None else None
    except (TypeError, ValueError):
        humidity = None

    delta = 0
    hourly = ((payload.get("hourly") or {}).get("temperature_2m")) or []
    if len(hourly) >= 3 and isinstance(hourly[0], (int, float)) \
            and isinstance(hourly[2], (int, float)):
        delta = int(round(float(hourly[2]) - float(hourly[0])))

    return WeatherNow(outdoor_c=outdoor, humidity=humidity, next2h_delta_c=delta)


#: IP geolocation, tried in order (design §7: `ipwho.is` / `ipinfo`). A single
#: provider is not enough: `ipapi.co` started answering a Cloudflare challenge
#: page instead of JSON, which silently turned every weather reply into
#: "没拿到天气" (2026-10-15).
GEO_URLS = (
    "https://ipwho.is/",
    "https://ipinfo.io/json",
)
FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,relative_humidity_2m"
    "&hourly=temperature_2m&forecast_days=1&timezone=auto"
)


def _get_json(url: str, timeout: float) -> dict:
    """One tiny JSON GET. Raises on any failure -- the caller decides."""
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _coords(payload: Any) -> Optional[tuple[float, float]]:
    """Read latitude/longitude out of either provider's shape.

    ``ipwho.is`` reports ``latitude``/``longitude``; ``ipinfo.io`` reports a
    ``"lat,lon"`` string under ``loc``. Anything else (a challenge page, an
    error object) yields ``None`` so the next provider gets a turn.
    """
    if not isinstance(payload, dict):
        return None

    try:
        return float(payload["latitude"]), float(payload["longitude"])
    except (KeyError, TypeError, ValueError):
        pass

    loc = payload.get("loc")
    if isinstance(loc, str) and "," in loc:
        latitude, _, longitude = loc.partition(",")
        try:
            return float(latitude), float(longitude)
        except ValueError:
            return None
    return None


def geolocate(*, timeout: float = 6.0) -> Optional[tuple[float, float]]:
    """Where we are, by public IP. ``None`` when every provider fails."""
    for url in GEO_URLS:
        try:
            coords = _coords(_get_json(url, timeout))
        except Exception as exc:  # noqa: BLE001 - one provider down is not fatal
            log.warning("geolocate: %s failed (%s), trying the next", url, exc)
            continue
        if coords is not None:
            return coords
        log.warning("geolocate: %s returned no coordinates", url)
    log.warning("geolocate: every provider failed")
    return None


def fetch_current(lat: float, lon: float, *, timeout: float = 8.0
                  ) -> Optional[WeatherNow]:
    """Outdoor conditions plus the two-hour trend, from Open-Meteo."""
    try:
        payload = _get_json(FORECAST_URL.format(lat=lat, lon=lon), timeout)
    except Exception:  # noqa: BLE001
        return None
    return parse_open_meteo(payload)


def fetch_now(*, lat: str = "", lon: str = "", timeout: float = 8.0
              ) -> Optional[WeatherNow]:
    """Locate (unless configured) and fetch, in one call the service can use."""
    if lat and lon:
        try:
            return fetch_current(float(lat), float(lon), timeout=timeout)
        except ValueError:
            return None
    located = geolocate(timeout=timeout)
    if located is None:
        return None
    return fetch_current(*located, timeout=timeout)
