from cyborg_hvac.backends.hisense_connectlife.mapping import (
    build_capabilities,
    build_properties,
    effective_fan_map,
    fan_map_for,
    format_temperature,
    installed_zones_hint,
    parse_status,
)
from cyborg_hvac.backends.hisense_connectlife.profiles import (
    decode_signed_nibble,
    encode_signed_nibble,
    get_profile,
    parse_property_list,
    zone_indices,
)
from cyborg_hvac.core.model import (
    Command,
    Feature,
    FeatureKind,
    HvacMode,
    SensorKind,
    ZoneCommand,
    ZoneState,
)


def caps_for(fixture):
    profile = get_profile("009", "19901")
    meta = parse_property_list(fixture["properties"])
    status = fixture["device"]["statusList"]
    return profile, meta, build_capabilities(profile, meta, status, fixture["static_data"])


def test_property_list_parsing():
    meta = parse_property_list(
        [
            {"propertyKey": "t_temp", "propertyValueList": "16~32,61~90"},
            {"propertyKey": "t_fan_speed", "propertyValueList": "0,5,7,9"},
            {"propertyKey": "z", "lowerLimit": "0", "upperLimit": "100", "step": "10"},
            {"bad": True},
        ]
    )
    assert meta["t_temp"].ranges == ((16, 32), (61, 90))
    assert meta["t_fan_speed"].values == ("0", "5", "7", "9")
    assert meta["z"].ranges == ((0, 100),)
    assert meta["z"].step == 10


def test_hacz01_capabilities(hacz01):
    _, _, caps = caps_for(hacz01)
    assert caps.hvac_modes == (
        HvacMode.FAN_ONLY,
        HvacMode.HEAT,
        HvacMode.COOL,
        HvacMode.DRY,
        HvacMode.AUTO,
    )
    # The property list names six speeds; the static gear selection keeps the
    # four the app shows, and Quiet adds the silent speed.
    assert caps.fan_speeds == ("auto", "silent", "low", "medium", "high")
    assert (caps.min_temp, caps.max_temp, caps.temp_step) == (16, 32, 0.5)
    assert caps.zone_slots == 8
    # The property list claims "0,1" for openings; the profile's 5% step wins.
    assert caps.zone_opening_step == 5
    # Beep, backlight, AI, offset and fresh air are switched off by static data.
    assert set(caps.features) == {
        Feature.POWERFUL,
        Feature.ECO,
        Feature.QUIET,
        Feature.SLEEP,
        Feature.FROST_GUARD,
        Feature.HEALTH,
    }
    # The cloud lists sleep gears, but the HACZ01 app toggles Sleep on and off.
    assert caps.features[Feature.SLEEP].kind is FeatureKind.SWITCH
    assert SensorKind.RETURN_AIR_TEMPERATURE in caps.sensors
    assert SensorKind.HUMIDITY not in caps.sensors
    assert not caps.has_filter_alert
    assert caps.has_fault_report


def test_sleep_gears_on_other_models(hacz01):
    profile = get_profile("009", "100")
    meta = parse_property_list(hacz01["properties"])
    status = hacz01["device"]["statusList"]
    caps = build_capabilities(profile, meta, status, hacz01["static_data"])
    assert caps.features[Feature.SLEEP].kind is FeatureKind.SELECT
    assert len(caps.features[Feature.SLEEP].options) == 5
    static = {**hacz01["static_data"], "Sleep_mode_supports_gears": "0"}
    caps = build_capabilities(profile, meta, status, static)
    assert caps.features[Feature.SLEEP].kind is FeatureKind.SWITCH


def test_static_flag_can_remove_powerful(hacz01):
    profile = get_profile("009", "19901")
    meta = parse_property_list(hacz01["properties"])
    caps = build_capabilities(
        profile, meta, hacz01["device"]["statusList"], {"Super_function": "0"}
    )
    assert Feature.POWERFUL not in caps.features


def test_parse_status(hacz01):
    profile, meta, caps = caps_for(hacz01)
    fan_map = effective_fan_map(profile, meta, hacz01["static_data"])
    state = parse_status(
        profile, caps, hacz01["device"]["statusList"], online=True, fan_map=fan_map
    )
    assert state.power is True
    assert state.mode is HvacMode.HEAT
    assert state.target_temp == 22.0
    assert state.current_temp == 21.0
    assert state.fan_speed == "auto"
    assert state.zones[1] == ZoneState(on=True, opening=100)
    assert state.zones[2] == ZoneState(on=True, opening=30)
    assert state.zones[6] == ZoneState(on=False, opening=0)
    assert state.features[Feature.ECO] is True
    # Not reported by the third-party API until first changed.
    assert Feature.SLEEP not in state.features
    assert state.sensors[SensorKind.VOLTAGE] == 237
    assert state.faults == ()
    assert state.filter_alert is None


def test_quiet_reads_as_silent_fan(hacz01):
    profile, _meta, caps = caps_for(hacz01)
    status = {**hacz01["device"]["statusList"], "t_fan_mute": "1", "t_fan_speed": "1"}
    state = parse_status(profile, caps, status, online=True)
    assert state.fan_speed == "silent"
    assert state.features[Feature.QUIET] is True


def test_faults_are_listed(hacz01):
    profile, _, caps = caps_for(hacz01)
    status = {**hacz01["device"]["statusList"], "f_e_incom": "1"}
    assert parse_status(profile, caps, status, online=True).faults == ("f_e_incom",)


def test_installed_zone_hint(hacz01):
    assert installed_zones_hint(hacz01["device"]["statusList"], 8) == (1, 2, 3, 4, 5)


def test_build_properties(hacz01):
    profile, meta, caps = caps_for(hacz01)
    fan_map = effective_fan_map(profile, meta, hacz01["static_data"])
    command = Command(
        power=True,
        mode=HvacMode.COOL,
        target_temp=21.5,
        fan_speed="medium",
        features={Feature.POWERFUL: True, Feature.SLEEP: True, Feature.TEMP_OFFSET: -3},
        zones={2: ZoneCommand(on=True, opening=33), 6: ZoneCommand(on=False)},
    )
    props = build_properties(profile, caps, command, fan_map)
    assert props == {
        "t_power": "1",
        "t_work_mode": "2",
        "t_temp": "21.5",
        "t_fan_speed": "7",
        "t_super": "1",
        "t_sleep": "1",
        "t_temp_compensate": "13",
        "aus_zone2_power": "1",
        "aus_zone2_opencontrol": "35",
        "aus_zone6_power": "0",
    }


def test_silent_fan_turns_quiet_on(hacz01):
    profile, meta, caps = caps_for(hacz01)
    fan_map = effective_fan_map(profile, meta, hacz01["static_data"])
    props = build_properties(profile, caps, Command(fan_speed="silent"), fan_map)
    assert props == {"t_fan_mute": "1"}


def test_whole_setpoints_are_sent_as_integers():
    assert format_temperature(22.0) == "22"
    assert format_temperature(21.5) == "21.5"
    assert format_temperature(21.26) == "21.5"


def test_signed_nibble_round_trip():
    for value in range(-7, 8):
        assert decode_signed_nibble(encode_signed_nibble(value)) == value
    assert encode_signed_nibble(-1) == "15"
    assert decode_signed_nibble("8") is None


def test_zone_indices_need_both_properties():
    keys = {"aus_zone1_power", "aus_zone1_opencontrol", "aus_zone2_power", "t_power"}
    assert zone_indices(keys) == [1]


def test_legacy_fan_codes_are_used_when_reported():
    profile = get_profile("009", "100")
    assert fan_map_for(profile, {"0", "2", "3", "4"}) == {
        "0": "auto",
        "2": "low",
        "3": "medium",
        "4": "high",
    }
