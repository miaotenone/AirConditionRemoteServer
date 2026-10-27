"""app.weather -- the daily suggestion, pure.

The threshold table is the user's own (design Q2): outdoors 35℃ and up suggests
26℃, and so on down to "just ventilate" and "heat". A forecast that is about to
get much cooler nudges the suggestion up a degree so nobody wakes up cold
(design Q2b).

The network calls live here too, but behind their own functions, so the whole
decision is unit tested from a fixed payload.
"""

from app import weather
from app.state import AC_MODE
from app.weather import WeatherNow, parse_open_meteo, recommend

COOL, FAN, HEAT = AC_MODE["cool"], AC_MODE["fan"], AC_MODE["heat"]


def test_very_hot_outdoors_suggests_26():
    rec = recommend(36)
    assert (rec.mode, rec.temp) == (COOL, 26)


def test_exactly_35_is_still_the_very_hot_band():
    assert recommend(35).temp == 26


def test_30_to_35_suggests_25():
    assert recommend(34).temp == 25
    assert recommend(30).temp == 25


def test_26_to_30_suggests_24():
    assert recommend(29).temp == 24
    assert recommend(26).temp == 24


def test_a_pleasant_18_to_26_suggests_fan_only():
    rec = recommend(25)
    assert rec.mode == FAN
    assert rec.temp_used is False


def test_below_18_suggests_heat():
    assert recommend(17).mode == HEAT
    assert recommend(18).mode == FAN


def test_a_coming_cold_snap_raises_the_cooling_target_by_one():
    assert recommend(36, trend_delta_c=-2).temp == 27


def test_a_small_dip_does_not_move_the_target():
    assert recommend(36, trend_delta_c=-1).temp == 26


def test_the_cold_snap_nudge_skips_the_fan_suggestion():
    # 送风本来就不设温度，没有「上调一度」这回事。
    assert recommend(25, trend_delta_c=-5).mode == FAN


def test_the_nudge_never_leaves_the_legal_range():
    assert recommend(36, trend_delta_c=-9).temp <= 30


def test_parse_open_meteo_reads_current_and_the_two_hour_ahead_hourly():
    payload = {
        "current": {"temperature_2m": 30.0, "relative_humidity_2m": 61},
        "hourly": {"time": ["t0", "t1", "t2"], "temperature_2m": [30.0, 29.0, 26.0]},
    }
    now = parse_open_meteo(payload)
    assert now == WeatherNow(outdoor_c=30, humidity=61, next2h_delta_c=-4)


def test_parse_open_meteo_survives_a_short_hourly_series():
    payload = {"current": {"temperature_2m": 30.0}, "hourly": {"temperature_2m": [30.0]}}
    now = parse_open_meteo(payload)
    assert now.next2h_delta_c == 0
    assert now.humidity is None


def test_parse_open_meteo_returns_none_without_a_current_temperature():
    assert parse_open_meteo({}) is None


# ------------------------------------------------------------- IP geolocation
# The first provider answers a Cloudflare challenge page in production (see the
# 2026-10-15 outage: `天气` said "没拿到" because `ipapi.co` stopped returning
# JSON). So the lookup is a chain: any provider may fail, the next one is tried.


def test_geolocate_reads_the_first_providers_latitude_longitude(monkeypatch):
    monkeypatch.setattr(
        weather, "_get_json", lambda url, timeout: {"latitude": 22.5, "longitude": 114.0}
    )
    assert weather.geolocate() == (22.5, 114.0)


def test_geolocate_falls_back_when_a_provider_breaks(monkeypatch):
    calls = []

    def fake_get_json(url, timeout):
        calls.append(url)
        if url == weather.GEO_URLS[0]:
            raise ValueError("Just a moment...")  # an HTML challenge, not JSON
        return {"loc": "22.3193,114.1694"}

    monkeypatch.setattr(weather, "_get_json", fake_get_json)
    assert weather.geolocate() == (22.3193, 114.1694)
    assert calls == list(weather.GEO_URLS[:2]), "第一个挂了就该试下一个"


def test_geolocate_reads_a_comma_separated_loc_too(monkeypatch):
    monkeypatch.setattr(weather, "_get_json", lambda url, timeout: {"loc": "1.5,2.5"})
    assert weather.geolocate() == (1.5, 2.5)


def test_geolocate_gives_up_when_every_provider_fails(monkeypatch):
    def always_fails(url, timeout):
        raise OSError("offline")

    monkeypatch.setattr(weather, "_get_json", always_fails)
    assert weather.geolocate() is None


# ------------------------------------------- the room the AC is actually in
# Design Q2c (2026-10-23): the table says what the *outdoors* suggests; that
# target is then checked against the DHT11 reading. A target the room already
# meets is "不用开"; one that came out on the wrong side of the room -- the
# reported "外面 16℃, 建议制热 20℃" in a 23℃ room, i.e. heat it *colder* -- is
# moved to the near side instead of being handed over as-is.


def test_the_room_is_named_when_it_is_known():
    rec = recommend(36, 0, room_c=30)
    assert "室温 30℃" in rec.note
    assert rec.temp == 26  # a real gap to close: the table stands
    assert rec.skip is False


def test_a_target_the_room_already_meets_is_not_worth_running():
    # 室外 16℃ → 表值制热 20℃；室温 21℃ → 开了也不制热。
    rec = recommend(16, 0, room_c=21)
    assert rec.skip is True
    assert "不用开" in rec.note


def test_a_heat_target_below_the_room_is_lifted_to_the_near_side():
    # 用户报的那一张：室外 16℃、室温 23℃，「建议制热 20℃」比房间还冷。
    rec = recommend(16, 0, room_c=23)
    assert rec.mode == HEAT
    assert rec.temp == 25  # 室温 + 2，这回是真的要把它加热
    assert "室温 23℃" in rec.note
    assert rec.skip is False


def test_a_cool_target_above_the_room_is_pulled_to_the_near_side():
    # 室外 27℃ → 表值制冷 24℃；室温 22℃ → 那台空调不会制冷。
    rec = recommend(27, 0, room_c=22)
    assert rec.mode == COOL
    assert rec.temp == 20  # 室温 - 2


def test_the_room_fix_respects_the_legal_range():
    assert recommend(16, 0, room_c=30).temp == 30  # 30+2 clamps to the max
    assert recommend(36, 0, room_c=16).temp == 16  # 16-2 clamps to the min


def test_without_a_room_reading_the_table_is_untouched():
    assert recommend(16).note == "外面 16℃，建议制热 20℃"
    assert recommend(16).skip is False


def test_the_two_hour_trend_starts_at_the_current_hour():
    """The hourly series starts at 00:00, so entries 0 and 2 are last night.

    Reading those made the cold-snap nudge a statement about 00:00→02:00 --
    something that has already happened -- instead of about the next two hours
    (design Q2b, fixed 2026-10-23). ``current.time`` carries minutes while the
    hourly stamps sit on the hour, which is what the live payload looks like.
    """
    payload = {
        "current": {"temperature_2m": 30.0, "time": "2026-10-23T15:20"},
        "hourly": {
            "time": [
                "2026-10-23T13:00",
                "2026-10-23T14:00",
                "2026-10-23T15:00",
                "2026-10-23T16:00",
                "2026-10-23T17:00",
            ],
            "temperature_2m": [10.0, 11.0, 30.0, 29.0, 28.0],
        },
    }
    now = parse_open_meteo(payload)
    assert now.next2h_delta_c == -2  # 15:00 → 17:00, not 13:00 → 15:00


def test_the_two_hour_trend_ignores_a_stamp_from_another_day():
    payload = {
        "current": {"temperature_2m": 20.0, "time": "2026-10-23T15:20"},
        "hourly": {
            "time": ["2026-10-22T15:00", "2026-10-22T16:00", "2026-10-22T17:00"],
            "temperature_2m": [30.0, 31.0, 32.0],
        },
    }
    # 昨天的 15:00 不是「现在」；找不到就当没有未来，别把旧的温差当趋势。
    assert parse_open_meteo(payload).next2h_delta_c == 0


def test_the_two_hour_trend_is_dropped_when_the_series_stops_at_now():
    payload = {
        "current": {"temperature_2m": 30.0, "time": "2026-10-23T23:00"},
        "hourly": {"time": ["2026-10-23T23:00"], "temperature_2m": [30.0]},
    }
    assert parse_open_meteo(payload).next2h_delta_c == 0
