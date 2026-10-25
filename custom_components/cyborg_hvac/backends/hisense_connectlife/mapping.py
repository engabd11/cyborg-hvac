"""Translate between ConnectLife properties and the core model."""

from __future__ import annotations

from typing import Any

from ...core.model import (
    FAN_SILENT,
    Capabilities,
    Command,
    Feature,
    FeatureKind,
    FeatureSpec,
    SensorKind,
    UnitState,
    ZoneState,
)
from .profiles import (
    ENCODING_SIGNED_NIBBLE,
    FAN_QUIET_CODE,
    FAULT_PREFIX,
    HUMIDITY_UNKNOWN,
    KEY_CURRENT,
    KEY_FAN,
    KEY_FILTER,
    KEY_HUMIDITY,
    KEY_MODE,
    KEY_POWER,
    KEY_TARGET,
    KEY_TEMP_UNIT,
    KEY_VOLTAGE,
    ZONE_OPENING_KEY,
    ZONE_POWER_KEY,
    FeatureProperty,
    Profile,
    PropertyMeta,
    decode_signed_nibble,
    encode_signed_nibble,
    zone_indices,
)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _feature_codes(prop: FeatureProperty) -> set[str]:
    if prop.kind is FeatureKind.SWITCH:
        return {prop.on_value, prop.off_value}
    if prop.kind is FeatureKind.SELECT and prop.options:
        return set(prop.options)
    return set()


def build_capabilities(
    profile: Profile,
    meta: dict[str, PropertyMeta],
    status: dict[str, Any],
    static: dict[str, Any] | None = None,
) -> Capabilities:
    """Work out what a unit supports from its profile, property list and status."""
    static = static or {}
    known_keys = set(meta) or set(status)

    def values_for(key: str) -> set[str] | None:
        item = meta.get(key)
        return set(item.values) if item and item.values else None

    mode_values = values_for(KEY_MODE)
    modes = tuple(
        mode for code, mode in profile.modes.items() if mode_values is None or code in mode_values
    )

    fan_map = effective_fan_map(profile, meta, static)
    fan_speeds = tuple(dict.fromkeys(fan_map.values()))

    fahrenheit = str(status.get(KEY_TEMP_UNIT, "0")) == "1"
    low, high = profile.temp_range_f if fahrenheit else profile.temp_range_c
    target_meta = meta.get(KEY_TARGET)
    if target_meta and target_meta.ranges:
        # The first range is Celsius and the second Fahrenheit when both exist.
        ranges = target_meta.ranges
        chosen = ranges[1] if fahrenheit and len(ranges) > 1 else ranges[0]
        low, high = chosen

    features: dict = {}
    for prop in profile.features:
        if prop.feature in profile.disabled_features or prop.key not in known_keys:
            continue
        if not _static_allows(prop, static):
            continue
        options: tuple[str, ...] = ()
        kind = prop.kind
        if prop.kind is FeatureKind.SELECT and prop.options:
            allowed = values_for(prop.key)
            if prop.key == "t_sleep" and str(static.get("Sleep_mode_supports_gears", "1")) == "0":
                allowed = set(list(prop.options)[:2])
            options = tuple(
                name for code, name in prop.options.items() if allowed is None or code in allowed
            )
            if allowed is not None and len(options) <= 2:
                # Off plus a single choice reads better as a switch.
                kind = FeatureKind.SWITCH
                options = ()
        features[prop.feature] = FeatureSpec(
            feature=prop.feature,
            kind=kind,
            options=options,
            min_value=prop.min_value,
            max_value=prop.max_value,
            step=prop.step,
            valid_modes=prop.valid_modes,
            requires_power=prop.requires_power,
            verified=prop.verified and prop.feature not in profile.unverified_features,
        )

    if Feature.QUIET in features and FAN_SILENT not in fan_speeds and fan_speeds:
        # Quiet doubles as the lowest fan speed, as on the wall controller.
        fan_speeds = (fan_speeds[0], FAN_SILENT, *fan_speeds[1:])

    temp_step = profile.temp_step
    if str(static.get("set_temperature_step_control", "")) == "2":
        temp_step = 0.5

    zones = [n for n in zone_indices(known_keys) if n <= profile.max_zone_slots]
    if str(static.get("Regional_control_function", "1")) == "0":
        zones = []
    opening_step = profile.zone_opening_step
    if zones:
        # Some models list zone openings as "0,1" with step 1, which is wrong;
        # trust the metadata only when it gives a real range and step.
        zone_meta = meta.get(ZONE_OPENING_KEY.format(n=zones[0]))
        if zone_meta and zone_meta.ranges and zone_meta.step and zone_meta.step > 1:
            opening_step = int(zone_meta.step)

    sensors = []
    if any(key in known_keys for key in KEY_CURRENT):
        sensors.append(SensorKind.RETURN_AIR_TEMPERATURE)
    if KEY_HUMIDITY in known_keys and str(status.get(KEY_HUMIDITY, "")) not in HUMIDITY_UNKNOWN:
        sensors.append(SensorKind.HUMIDITY)
    if KEY_VOLTAGE in known_keys or KEY_VOLTAGE in status:
        sensors.append(SensorKind.VOLTAGE)

    return Capabilities(
        hvac_modes=modes,
        fan_speeds=fan_speeds,
        min_temp=low,
        max_temp=high,
        temp_step=temp_step,
        temperature_unit="F" if fahrenheit else "C",
        features=features,
        sensors=tuple(sensors),
        zone_slots=max(zones) if zones else 0,
        zone_opening_step=opening_step,
        has_filter_alert=any(key in status or key in known_keys for key in KEY_FILTER),
        has_fault_report=any(key.startswith(FAULT_PREFIX) for key in known_keys | set(status)),
    )


# Static data flags that switch a feature off when explicitly "0".
_STATIC_FLAGS = {
    "t_super": "Super_function",
    "t_fan_mute": "Mute_mode_function",
    "t_eco": "Energy_saving_function_control",
    "t_8heat": "eight_heating_control",
    "t_beep": "buzzer_switch",
    "t_dimmer": "Backlight_control",
    "t_tms": "TMS_start_stop_control",
    # The offset applies to the AI function only.
    "t_temp_compensate": "TMS_start_stop_control",
    "t_fresh_air": "t_fresh_air",
}

# Fan codes for the static "Wind_speed_gear_selection" value.
_FAN_GEARS = {
    "7": {"0", "5", "7", "9"},
    "9": {"0", "5", "6", "7", "8", "9"},
}


def _static_allows(prop: FeatureProperty, static: dict[str, Any]) -> bool:
    flag = _STATIC_FLAGS.get(prop.key)
    return flag is None or str(static.get(flag, "1")) != "0"


def effective_fan_map(
    profile: Profile, meta: dict[str, PropertyMeta], static: dict[str, Any] | None
) -> dict[str, str]:
    """Return the fan codes a unit really offers.

    The property list may name more speeds than the unit has; the static gear
    selection narrows them to what the vendor app shows.
    """
    fan_meta = meta.get(KEY_FAN)
    values = set(fan_meta.values) if fan_meta and fan_meta.values else None
    gears = _FAN_GEARS.get(str((static or {}).get("Wind_speed_gear_selection", "")))
    if gears is not None:
        values = gears if values is None else values & gears
    return fan_map_for(profile, values)


def fan_map_for(profile: Profile, fan_values: set[str] | None) -> dict[str, str]:
    """Return the fan code map that matches the codes the device reports."""
    if fan_values is None:
        return dict(profile.fan_speeds)
    primary = {c: n for c, n in profile.fan_speeds.items() if c in fan_values}
    if len(primary) > 1:
        return primary
    legacy = {c: n for c, n in profile.fan_speeds_legacy.items() if c in fan_values}
    return legacy if len(legacy) > len(primary) else primary


def installed_zones_hint(status: dict[str, Any], slots: int) -> tuple[int, ...]:
    """Guess which zones are installed: zones that are on or have an opening."""
    hint = []
    for n in range(1, slots + 1):
        power = str(status.get(ZONE_POWER_KEY.format(n=n), "0"))
        opening = _to_float(status.get(ZONE_OPENING_KEY.format(n=n))) or 0
        if power == "1" or opening > 0:
            hint.append(n)
    return tuple(hint)


def parse_status(
    profile: Profile,
    caps: Capabilities,
    status: dict[str, Any],
    *,
    online: bool,
    fan_map: dict[str, str] | None = None,
) -> UnitState:
    """Turn a ConnectLife status dict into a UnitState."""
    status = {key: str(value) for key, value in status.items() if value is not None}
    fan_map = fan_map or fan_map_for(profile, None)

    target = _to_float(status.get(KEY_TARGET))
    current = None
    for key in KEY_CURRENT:
        current = _to_float(status.get(key))
        if current is not None:
            break

    features: dict = {}
    for prop in profile.features:
        if prop.feature not in caps.features or prop.key not in status:
            continue
        raw = status[prop.key]
        spec = caps.features[prop.feature]
        if prop.kind is FeatureKind.SWITCH:
            features[prop.feature] = raw == prop.on_value
        elif prop.kind is FeatureKind.SELECT and spec.kind is FeatureKind.SWITCH:
            features[prop.feature] = raw not in ("", next(iter(prop.options or {"0": ""})))
        elif prop.kind is FeatureKind.SELECT and prop.options:
            features[prop.feature] = prop.options.get(raw, next(iter(prop.options.values())))
        elif prop.encoding == ENCODING_SIGNED_NIBBLE:
            features[prop.feature] = decode_signed_nibble(raw)
        else:
            features[prop.feature] = _to_float(raw)

    zones = {}
    for n in range(1, caps.zone_slots + 1):
        power_key = ZONE_POWER_KEY.format(n=n)
        if power_key not in status:
            continue
        opening = _to_float(status.get(ZONE_OPENING_KEY.format(n=n))) or 0
        zones[n] = ZoneState(on=status[power_key] == "1", opening=int(opening))

    sensors: dict = {}
    if SensorKind.RETURN_AIR_TEMPERATURE in caps.sensors:
        sensors[SensorKind.RETURN_AIR_TEMPERATURE] = current
    if SensorKind.HUMIDITY in caps.sensors:
        raw_humidity = status.get(KEY_HUMIDITY, "")
        sensors[SensorKind.HUMIDITY] = (
            None if raw_humidity in HUMIDITY_UNKNOWN else _to_float(raw_humidity)
        )
    if SensorKind.VOLTAGE in caps.sensors:
        sensors[SensorKind.VOLTAGE] = _to_float(status.get(KEY_VOLTAGE))

    filter_alert = None
    for key in KEY_FILTER:
        if key in status:
            filter_alert = status[key] not in ("0", "")
            break

    faults = tuple(
        sorted(
            key
            for key, value in status.items()
            if key.startswith(FAULT_PREFIX) and value not in ("0", "")
        )
    )

    return UnitState(
        online=online,
        power=status.get(KEY_POWER) == "1",
        mode=profile.modes.get(status.get(KEY_MODE, "")),
        target_temp=target if target else None,
        current_temp=current,
        fan_speed=(
            FAN_SILENT
            if status.get(KEY_FAN) == FAN_QUIET_CODE
            else fan_map.get(status.get(KEY_FAN, ""))
        ),
        features=features,
        zones=zones,
        sensors=sensors,
        filter_alert=filter_alert,
        faults=faults,
        raw=status,
    )


def format_temperature(value: float) -> str:
    """Format a setpoint: whole numbers as integers, halves with one decimal."""
    rounded = round(value * 2) / 2
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:.1f}"


def build_properties(
    profile: Profile,
    caps: Capabilities,
    command: Command,
    fan_map: dict[str, str] | None = None,
) -> dict[str, str]:
    """Turn a Command into the property writes ConnectLife expects."""
    props: dict[str, str] = {}
    if command.power is not None:
        props[KEY_POWER] = "1" if command.power else "0"
    if command.mode is not None:
        code = next((c for c, m in profile.modes.items() if m is command.mode), None)
        if code is not None:
            props[KEY_MODE] = code
    if command.target_temp is not None:
        props[KEY_TARGET] = format_temperature(caps.round_temp(command.target_temp))
    if command.fan_speed == FAN_SILENT and FAN_SILENT not in (fan_map or {}).values():
        props["t_fan_mute"] = "1"
    elif command.fan_speed is not None:
        fan_map = fan_map or fan_map_for(profile, None)
        allowed = set(caps.fan_speeds)
        code = next(
            (c for c, name in fan_map.items() if name == command.fan_speed and name in allowed),
            None,
        )
        if code is not None:
            props[KEY_FAN] = code

    by_feature = {prop.feature: prop for prop in profile.features}
    for feature, value in command.features.items():
        prop = by_feature.get(feature)
        if prop is None:
            continue
        if prop.kind is FeatureKind.SWITCH:
            props[prop.key] = prop.on_value if value else prop.off_value
        elif prop.kind is FeatureKind.SELECT and prop.options and isinstance(value, bool):
            # A select shown as a switch: off is the first code, on the second.
            codes = list(prop.options)
            props[prop.key] = codes[1] if value and len(codes) > 1 else codes[0]
        elif prop.kind is FeatureKind.SELECT and prop.options:
            code = next((c for c, name in prop.options.items() if name == value), None)
            if code is not None:
                props[prop.key] = code
        elif prop.encoding == ENCODING_SIGNED_NIBBLE:
            props[prop.key] = encode_signed_nibble(float(value))
        else:
            props[prop.key] = str(round(float(value)))

    for n, zone in command.zones.items():
        if zone.on is not None:
            props[ZONE_POWER_KEY.format(n=n)] = "1" if zone.on else "0"
        if zone.opening is not None:
            # Zone controllers reject decimal strings, so always send integers.
            props[ZONE_OPENING_KEY.format(n=n)] = str(caps.round_opening(zone.opening))
    return props


def apply_properties(status: dict[str, Any], properties: dict[str, Any]) -> dict[str, str]:
    """Return a status dict with pushed or written properties merged in."""
    merged = {key: str(value) for key, value in status.items()}
    merged.update({key: str(value) for key, value in properties.items()})
    return merged
