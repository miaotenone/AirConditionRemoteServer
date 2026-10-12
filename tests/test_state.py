"""Unit tests for app.state -- pure, no MQTT / Feishu / SQLite needed.

These describe the intended interface: everything here takes plain values and
returns plain values, so the whole module is testable without any I/O.
"""

from app.state import (
    AC_FAN,
    AC_MODE,
    TEMP_MAX,
    TEMP_MIN,
    AcState,
    Presence,
    apply_action,
    from_payload,
    is_stale,
    merge_actions,
    presence_from_state,
    presence_from_status,
    to_command,
)

# ---------------------------------------------------------------- from_payload


def test_from_payload_reads_the_four_state_fields():
    st = from_payload({"online": True, "power": 1, "mode": 2, "temp": 26, "fan": 3, "brand": 16})
    assert (st.power, st.mode, st.temp, st.fan) == (1, 2, 26, 3)


def test_from_payload_keeps_defaults_for_missing_fields():
    st = from_payload({"power": 1})
    assert (st.power, st.mode, st.temp, st.fan) == (1, AC_MODE["cool"], 24, AC_FAN["auto"])


def test_from_payload_clamps_out_of_range_values():
    assert from_payload({"temp": 99}).temp == TEMP_MAX
    assert from_payload({"temp": 0}).temp == TEMP_MIN
    assert from_payload({"mode": 99}).mode == 4
    assert from_payload({"fan": 99}).fan == 3


def test_from_payload_ignores_non_state_keys():
    st = from_payload({"online": True, "brand": 16, "whatever": 5})
    assert st == AcState()


# --------------------------------------------------------------- apply_action


def test_apply_action_power():
    assert apply_action(AcState(power=0), "power", 1).power == 1


def test_apply_action_mode_sets_mode():
    assert apply_action(AcState(mode=1), "mode", 4).mode == 4


def test_apply_action_temp_clamps_high_and_low():
    assert apply_action(AcState(temp=24), "temp", 99).temp == TEMP_MAX
    assert apply_action(AcState(temp=24), "temp", 1).temp == TEMP_MIN


def test_apply_action_temp_step_is_relative():
    assert apply_action(AcState(temp=24), "temp_step", -1).temp == 23
    assert apply_action(AcState(temp=24), "temp_step", 1).temp == 25


def test_apply_action_temp_step_clamps_at_the_boundary():
    assert apply_action(AcState(temp=TEMP_MAX), "temp_step", 1).temp == TEMP_MAX
    assert apply_action(AcState(temp=TEMP_MIN), "temp_step", -1).temp == TEMP_MIN


def test_apply_action_unknown_leaves_state_untouched():
    st = AcState(power=1, mode=2, temp=23, fan=3)
    assert apply_action(st, "explode", 1) == st


def test_apply_action_does_not_mutate_its_input():
    st = AcState(temp=24)
    apply_action(st, "temp", 30)
    assert st.temp == 24


# ------------------------------------------------------------------ to_command


def test_to_command_power_and_mode_are_absolute():
    assert to_command(AcState(), "power", 1) == {"power": 1}
    assert to_command(AcState(), "mode", 2) == {"mode": 2}


def test_to_command_temp_is_absolute():
    assert to_command(AcState(temp=24), "temp", 26) == {"temp": 26}


def test_to_command_temp_step_resolves_to_an_absolute_temp():
    assert to_command(AcState(temp=24), "temp_step", 1) == {"temp": 25}


def test_to_command_clamps_before_sending():
    assert to_command(AcState(), "temp", 99) == {"temp": TEMP_MAX}


def test_to_command_refresh_publishes_nothing():
    assert to_command(AcState(), "refresh", 0) == {}


def test_to_command_unknown_action_returns_none():
    assert to_command(AcState(), "nope", 1) is None


def test_to_command_never_emits_fields_it_does_not_know():
    # spec 4.2: the service must never send unknown fields downstream
    for action, value in (("power", 1), ("mode", 3), ("temp", 20), ("fan", 2), ("temp_step", -1)):
        assert set(to_command(AcState(), action, value)) <= {"power", "mode", "temp", "fan"}


# --------------------------------------------------------------- merge_actions


def test_merge_actions_coalesces_repeated_temp_steps():
    # three quick "+" taps from 24 must collapse to ONE command carrying 27
    payload = merge_actions(AcState(temp=24), [("temp_step", 1), ("temp_step", 1), ("temp_step", 1)])
    assert payload == {"temp": 27}


def test_merge_actions_clamps_the_coalesced_steps():
    payload = merge_actions(AcState(temp=29), [("temp_step", 1), ("temp_step", 1), ("temp_step", 1)])
    assert payload == {"temp": TEMP_MAX}


def test_merge_actions_combines_different_fields():
    payload = merge_actions(AcState(power=0, temp=24), [("power", 1), ("temp_step", 1)])
    assert payload == {"power": 1, "temp": 25}


def test_merge_actions_last_absolute_wins():
    payload = merge_actions(AcState(temp=24), [("temp", 26), ("temp", 28)])
    assert payload == {"temp": 28}


def test_merge_actions_ignores_unknown_actions():
    assert merge_actions(AcState(temp=24), [("bogus", 1)]) == {}


# ----------------------------------------------------------------- fingerprint


def test_fingerprint_is_stable_for_equal_states():
    assert AcState(power=1, temp=24).fingerprint() == AcState(power=1, temp=24).fingerprint()


def test_fingerprint_changes_when_a_field_changes():
    assert AcState(temp=24).fingerprint() != AcState(temp=25).fingerprint()


# -------------------------------------------------------------------- presence


def test_presence_from_status_marks_offline():
    p = presence_from_status(Presence(online=True), {"online": False})
    assert p.online is False


def test_presence_from_status_marks_online():
    assert presence_from_status(Presence(online=False), {"online": True}).online is True


def test_presence_from_state_stamps_the_arrival_time():
    p = presence_from_state(Presence(online=False, last_state_at=0.0), now=123.0)
    assert (p.online, p.last_state_at) == (True, 123.0)


def test_is_stale_only_when_online_but_old():
    fresh = Presence(online=True, last_state_at=100.0)
    assert is_stale(fresh, now=150.0, max_age=90.0) is False
    assert is_stale(fresh, now=200.0, max_age=90.0) is True


def test_is_stale_false_when_offline():
    # offline is reported by the LWT, not by staleness
    assert is_stale(Presence(online=False, last_state_at=0.0), now=10_000.0, max_age=90.0) is False
