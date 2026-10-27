"""Weather-based suggestions -- the decision is pure, the fetching is not.

Thresholds are the user's own table (design Q2): outdoors 35℃ and up suggests
26℃ cooling, down through "just ventilate" to "heat". A forecast about to get
much cooler nudges the suggestion up a degree (design Q2b). When the DHT11
reading is known the table's target is checked against the room it would act on
(design Q2c): a target the room already meets is "don't bother", and one that
came out on the wrong side of the room is moved to the near side.

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

#: How close the room may sit to a target before running the AC would be
#: pointless -- the table's target is then already met (design Q2c).
COMFORT_BAND = 1

#: Where a target that landed on the wrong side of the room is moved to: the AC
#: only ever gets a suggestion with a real gap to close (design Q2c).
ROOM_MARGIN = 2


@dataclass(frozen=True)
class Recommendation:
    """A suggested AC setting, plus a short line for the card."""

    mode: int
    temp: int
    fan: int
    note: str
    #: True when the room is already where the table would put it: the card says
    #: so and "立即执行" sends nothing (design Q2c).
    skip: bool = False

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


def actions(recommendation: Recommendation) -> tuple[tuple[str, int], ...]:
    """Canonical execution order; fan-only advice never sets temperature.

    A comfortable room must not be switched on. A non-skipped suggestion does
    switch on first, matching the existing service and preset command order.
    Recommendation.fan is intentionally not sent: this is the legacy contract.
    """
    if recommendation.skip:
        return ()
    commands = (("power", 1), ("mode", recommendation.mode))
    if recommendation.temp_used:
        commands += (("temp", recommendation.temp),)
    return commands


def recommend(
    outdoor_c: float, trend_delta_c: float = 0, room_c: Optional[float] = None
) -> Recommendation:
    """Turn an outdoor temperature into a suggested AC setting.

    Bands (design Q2): ``>=35 -> 26``, ``30..35 -> 25``, ``26..30 -> 24``,
    ``18..26 -> fan only``, ``<18 -> heat``. A coming cold snap raises the
    cooling/heating target by one degree (design Q2b).

    With a DHT11 reading in hand the target is also checked against the room it
    would act on (design Q2c, 2026-10-23). The report that prompted it: outdoors
    16℃ and a 23℃ room produced "建议制热 20℃", i.e. heat the room *colder* than
    it already is. So a target the room already meets comes back as "不用开",
    and one on the wrong side of the room is moved `ROOM_MARGIN` to the useful
    side. ``room_c=None`` keeps the plain table, which is what a service with no
    DHT11 has to go on.
    """
    outdoor = int(round(outdoor_c))

    if outdoor >= 35:
        mode, temp, advice = COOL, 26, "建议制冷 26℃"
    elif outdoor >= 30:
        mode, temp, advice = COOL, 25, "建议制冷 25℃"
    elif outdoor >= 26:
        mode, temp, advice = COOL, 24, "建议制冷 24℃"
    elif outdoor >= 18:
        mode, temp, advice = FAN, TEMP_DEFAULT, "凉爽，建议送风通风"
    else:
        mode, temp, advice = HEAT, 20, "建议制热 20℃"

    if mode != FAN and trend_delta_c <= COLD_SNAP_DELTA:
        temp = clamp_temp(temp + 1)
        advice += "（两小时内要降温，上调 1℃）"

    room = None if room_c is None else int(round(room_c))
    if room is None:
        return Recommendation(mode=mode, temp=temp, fan=0, note=f"外面 {outdoor}℃，{advice}")

    if mode != FAN:
        #: Positive means the target is warmer than the room.
        gap = temp - room
        if abs(gap) <= COMFORT_BAND:
            return Recommendation(
                mode=FAN,
                temp=TEMP_DEFAULT,
                fan=0,
                skip=True,
                note=f"外面 {outdoor}℃，室温 {room}℃ 已经在 {temp}℃ 附近，不用开空调",
            )
        heating = mode == HEAT
        if (heating and gap < 0) or (not heating and gap > 0):
            near = room + ROOM_MARGIN if heating else room - ROOM_MARGIN
            temp = clamp_temp(near)
            advice = f"建议{'制热' if heating else '制冷'} {temp}℃"

    return Recommendation(
        mode=mode, temp=temp, fan=0, note=f"外面 {outdoor}℃，室温 {room}℃，{advice}"
    )


# ------------------------------------------------------------------- fetching


def _next2h_delta(current: dict, hourly: dict) -> int:
    """The change over the two hours *after* the current one, in whole degrees.

    The series starts at 00:00, so reading the first and third entries measured
    00:00→02:00 -- something that has already happened -- and the nudge was
    never about the next two hours (design Q2b, fixed 2026-10-23). The current
    hour is located by its ``current.time`` stamp, compared on the hour because
    that stamp carries minutes and the hourly ones do not. A payload with no
    stamp at all cannot say where "now" is: the oldest fixtures look like that
    and are still read from the first entries.
    """
    temps = hourly.get("temperature_2m") or []
    if not isinstance(temps, list) or len(temps) < 3:
        return 0

    times = hourly.get("time") or []
    if not isinstance(times, list):
        times = []
    stamp = current.get("time")
    if isinstance(stamp, str):
        # Compare on the hour: `current.time` carries minutes ("16:15") while
        # the hourly stamps sit on the hour ("16:00"), which is exactly how the
        # live payload looks. A stamp we cannot place in the series means we
        # cannot see the next two hours either -- no nudge, guessed from
        # nothing.
        hour = stamp[:13]
        index = next(
            (i for i, t in enumerate(times) if isinstance(t, str) and t[:13] == hour), None
        )
        if index is None:
            return 0
    else:
        # No stamp: the payload cannot say which hour is "now". The older
        # fixtures look like this, and they were read from the first entries.
        index = 0

    if index + 2 >= len(temps):
        return 0  # the series stops before "in two hours"

    here, there = temps[index], temps[index + 2]
    if not isinstance(here, (int, float)) or not isinstance(there, (int, float)):
        return 0
    return int(round(float(there) - float(here)))


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

    delta = _next2h_delta(current, payload.get("hourly") or {})

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


def fetch_current(lat: float, lon: float, *, timeout: float = 8.0) -> Optional[WeatherNow]:
    """Outdoor conditions plus the two-hour trend, from Open-Meteo."""
    try:
        payload = _get_json(FORECAST_URL.format(lat=lat, lon=lon), timeout)
    except Exception:  # noqa: BLE001
        return None
    return parse_open_meteo(payload)


def fetch_now(*, lat: str = "", lon: str = "", timeout: float = 8.0) -> Optional[WeatherNow]:
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
