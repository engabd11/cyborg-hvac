from cyborg_hvac.core.model import (
    Capabilities,
    Command,
    Feature,
    FeatureKind,
    FeatureSpec,
    HvacMode,
    UnitState,
    ZoneCommand,
    ZoneState,
)


def test_merge_later_values_win_and_zones_combine():
    first = Command(power=True, target_temp=21, zones={1: ZoneCommand(on=True)})
    second = Command(target_temp=22, zones={1: ZoneCommand(opening=40), 2: ZoneCommand(on=False)})
    merged = first.merge(second)
    assert merged.power is True
    assert merged.target_temp == 22
    assert merged.zones[1] == ZoneCommand(on=True, opening=40)
    assert merged.zones[2] == ZoneCommand(on=False)


def test_apply_and_satisfied_by():
    state = UnitState(power=False, mode=HvacMode.HEAT, zones={1: ZoneState(on=False, opening=50)})
    command = Command(power=True, zones={1: ZoneCommand(on=True)}, features={Feature.ECO: True})
    result = command.apply_to(state)
    assert result.power is True
    assert result.zones[1] == ZoneState(on=True, opening=50)
    assert result.features[Feature.ECO] is True
    assert command.satisfied_by(result)
    assert not command.satisfied_by(state)


def test_empty_command():
    assert Command().is_empty
    assert not Command(zones={1: ZoneCommand(on=True)}).is_empty


def test_rounding_clamps_to_limits():
    caps = Capabilities(
        hvac_modes=(), zone_opening_step=10, temp_step=0.5, min_temp=16, max_temp=32
    )
    assert caps.round_opening(33) == 30
    assert caps.round_opening(36) == 40
    assert caps.round_opening(140) == 100
    assert caps.round_opening(-5) == 0
    assert caps.round_temp(21.3) == 21.5
    assert caps.round_temp(12) == 16


def test_feature_spec_accepts():
    spec = FeatureSpec(
        Feature.POWERFUL,
        FeatureKind.SWITCH,
        valid_modes=frozenset({HvacMode.HEAT, HvacMode.COOL}),
    )
    assert spec.accepts(True, HvacMode.HEAT)
    assert not spec.accepts(False, HvacMode.HEAT)
    assert not spec.accepts(True, HvacMode.FAN_ONLY)
    always = FeatureSpec(Feature.DISPLAY, FeatureKind.SWITCH, requires_power=False)
    assert always.accepts(False, None)
