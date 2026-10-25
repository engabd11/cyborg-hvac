"""Property maps for ConnectLife air conditioners.

A profile maps ConnectLife property keys and codes onto the core model. The base
profile covers device type 009 (split and ducted). Overrides keyed by
``"<type>-<feature>"`` adjust it for specific models. The cloud's property list
then narrows every map to what the device actually reports.

Property names and codes are interoperability facts collected from device dumps
and the ConnectLife app; see docs/supported-devices.md for their sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Any

from ...core.model import Feature, FeatureKind, HvacMode

ZONE_POWER_KEY = "aus_zone{n}_power"
ZONE_OPENING_KEY = "aus_zone{n}_opencontrol"
ZONE_KEY_RE = re.compile(r"^aus_zone(\d+)_(power|opencontrol)$")

KEY_POWER = "t_power"
KEY_MODE = "t_work_mode"
KEY_TARGET = "t_temp"
KEY_FAN = "t_fan_speed"
# While Quiet is on the unit reports this fan code; it is shown as the silent speed.
FAN_QUIET_CODE = "1"
KEY_TEMP_UNIT = "t_temp_type"
KEY_CURRENT = ("f_temp_in", "t_temp_in")
KEY_HUMIDITY = "f_humidity"
KEY_VOLTAGE = "f_votage"
KEY_FILTER = ("f-filter", "f_filter")
FAULT_PREFIX = "f_e_"
HUMIDITY_UNKNOWN = {"128", "0", "255", ""}

ENCODING_SIGNED_NIBBLE = "signed_nibble"


@dataclass(frozen=True, slots=True)
class FeatureProperty:
    """How one core feature maps onto a ConnectLife property."""

    feature: Feature
    key: str
    kind: FeatureKind = FeatureKind.SWITCH
    on_value: str = "1"
    off_value: str = "0"
    options: dict[str, str] | None = None
    encoding: str | None = None
    min_value: float = 0
    max_value: float = 0
    step: float = 1
    valid_modes: frozenset[HvacMode] | None = None
    requires_power: bool = True
    verified: bool = True


@dataclass(frozen=True, slots=True)
class Profile:
    """Property map for one device model."""

    name: str
    modes: dict[str, HvacMode]
    fan_speeds: dict[str, str]
    fan_speeds_legacy: dict[str, str] = field(default_factory=dict)
    temp_range_c: tuple[float, float] = (16, 32)
    temp_range_f: tuple[float, float] = (61, 90)
    temp_step: float = 1
    features: tuple[FeatureProperty, ...] = ()
    max_zone_slots: int = 8
    zone_opening_step: int = 5
    model: str | None = None
    # Features known to exist only in the property list, not on the device.
    disabled_features: frozenset[Feature] = frozenset()
    # Features listed by the device but not yet confirmed to work on it.
    unverified_features: frozenset[Feature] = frozenset()


_HEAT_COOL = frozenset({HvacMode.HEAT, HvacMode.COOL})

BASE_009 = Profile(
    name="009",
    modes={
        "0": HvacMode.FAN_ONLY,
        "1": HvacMode.HEAT,
        "2": HvacMode.COOL,
        "3": HvacMode.DRY,
        "4": HvacMode.AUTO,
    },
    fan_speeds={
        "0": "auto",
        "5": "low",
        "6": "medium_low",
        "7": "medium",
        "8": "medium_high",
        "9": "high",
    },
    fan_speeds_legacy={"0": "auto", "2": "low", "3": "medium", "4": "high"},
    features=(
        FeatureProperty(Feature.POWERFUL, "t_super", valid_modes=_HEAT_COOL),
        FeatureProperty(
            Feature.ECO,
            "t_eco",
            valid_modes=frozenset({HvacMode.HEAT, HvacMode.COOL, HvacMode.DRY, HvacMode.FAN_ONLY}),
        ),
        FeatureProperty(
            Feature.QUIET,
            "t_fan_mute",
            valid_modes=frozenset({HvacMode.HEAT, HvacMode.COOL, HvacMode.FAN_ONLY}),
        ),
        FeatureProperty(
            Feature.SLEEP,
            "t_sleep",
            kind=FeatureKind.SELECT,
            options={
                "0": "off",
                "1": "general",
                "2": "for_old",
                "3": "for_young",
                "4": "for_kid",
            },
            verified=False,
        ),
        FeatureProperty(Feature.FROST_GUARD, "t_8heat", valid_modes=frozenset({HvacMode.HEAT})),
        FeatureProperty(Feature.AI, "t_tms", verified=False),
        FeatureProperty(Feature.HEALTH, "t_purify", verified=False),
        FeatureProperty(Feature.FRESH_AIR, "t_fresh_air", verified=False),
        FeatureProperty(
            Feature.DISPLAY,
            "t_dimmer",
            on_value="0",
            off_value="1",
            requires_power=False,
            verified=False,
        ),
        FeatureProperty(Feature.BEEP, "t_beep", requires_power=False, verified=False),
        FeatureProperty(
            Feature.TEMP_OFFSET,
            "t_temp_compensate",
            kind=FeatureKind.NUMBER,
            encoding=ENCODING_SIGNED_NIBBLE,
            min_value=-7,
            max_value=7,
            step=1,
            requires_power=False,
            verified=False,
        ),
    ),
)


def _replace_features(
    features: tuple[FeatureProperty, ...], *replacements: FeatureProperty
) -> tuple[FeatureProperty, ...]:
    by_feature = {prop.feature: prop for prop in replacements}
    return tuple(by_feature.pop(prop.feature, prop) for prop in features) + tuple(
        by_feature.values()
    )


# Overrides applied on top of the base profile for a device type.
_OVERRIDES: dict[str, dict[str, Any]] = {
    # Hisense ducted: HACZ01 Wi-Fi zone controller with HAAZ01 zone board (8 zones).
    # The ConnectLife app shows four fan speeds, Sleep as an on/off toggle, and
    # Powerful, Quiet, Health, Energy Saving and 8 degree heat. Other listed
    # properties have no control in the app.
    "009-19901": {
        "name": "009-19901",
        "model": "HACZ01 ducted zone controller",
        "temp_step": 0.5,
        # The dampers move in 5% steps (confirmed on the wall controller).
        "zone_opening_step": 5,
        "fan_speeds": {"0": "auto", "5": "low", "7": "medium", "9": "high"},
        "features": _replace_features(
            BASE_009.features,
            # The cloud lists sleep gears 0 to 4, but the app only toggles 0 and 1.
            FeatureProperty(
                Feature.SLEEP,
                "t_sleep",
                kind=FeatureKind.SELECT,
                options={"0": "off", "1": "general"},
            ),
            FeatureProperty(Feature.HEALTH, "t_purify"),
        ),
        "disabled_features": frozenset(
            {Feature.AI, Feature.FRESH_AIR, Feature.DISPLAY, Feature.BEEP}
        ),
        "unverified_features": frozenset({Feature.TEMP_OFFSET}),
    },
}

_BASE_BY_TYPE: dict[str, Profile] = {"009": BASE_009}

SUPPORTED_TYPES = frozenset(_BASE_BY_TYPE)


def get_profile(type_code: str, feature_code: str) -> Profile:
    """Return the profile for a device model."""
    base = _BASE_BY_TYPE.get(type_code, BASE_009)
    override = _OVERRIDES.get(f"{type_code}-{feature_code}")
    return replace(base, **override) if override else base


@dataclass(frozen=True, slots=True)
class PropertyMeta:
    """Metadata for one property from the cloud's property list."""

    key: str
    values: tuple[str, ...] = ()
    ranges: tuple[tuple[float, float], ...] = ()
    step: float | None = None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_property_list(items: list[dict[str, Any]]) -> dict[str, PropertyMeta]:
    """Parse the cloud's property list into metadata keyed by property."""
    result: dict[str, PropertyMeta] = {}
    for item in items:
        key = item.get("propertyKey")
        if not isinstance(key, str):
            continue
        values: list[str] = []
        ranges: list[tuple[float, float]] = []
        raw = item.get("propertyValueList")
        if isinstance(raw, str):
            for part in raw.split(","):
                part = part.strip()
                if "~" in part:
                    low, _, high = part.partition("~")
                    low_f, high_f = _to_float(low), _to_float(high)
                    if low_f is not None and high_f is not None:
                        ranges.append((low_f, high_f))
                elif part:
                    values.append(part)
        low_f, high_f = _to_float(item.get("lowerLimit")), _to_float(item.get("upperLimit"))
        if not ranges and low_f is not None and high_f is not None and high_f > low_f:
            ranges.append((low_f, high_f))
        step = _to_float(item.get("step"))
        result[key] = PropertyMeta(
            key=key,
            values=tuple(values),
            ranges=tuple(ranges),
            step=step if step and step > 0 else None,
        )
    return result


def zone_indices(keys: set[str] | dict[str, Any]) -> list[int]:
    """Return the zone numbers that have both a power and an opening property."""
    found: dict[int, set[str]] = {}
    for key in keys:
        match = ZONE_KEY_RE.match(key)
        if match:
            found.setdefault(int(match.group(1)), set()).add(match.group(2))
    return sorted(n for n, kinds in found.items() if kinds == {"power", "opencontrol"})


def encode_signed_nibble(value: float) -> str:
    """Encode -7..+7 as the 4-bit code the unit uses (negative as 16 + value)."""
    number = max(-7, min(7, round(value)))
    return str(number if number >= 0 else 16 + number)


def decode_signed_nibble(raw: str | None) -> float | None:
    """Decode the 4-bit offset code back to -7..+7."""
    number = _to_float(raw)
    if number is None:
        return None
    code = int(number)
    if 0 <= code <= 7:
        return float(code)
    if 9 <= code <= 15:
        return float(code - 16)
    return None
