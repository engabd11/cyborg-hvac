"""Constants for Cyborg HVAC."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "cyborg_hvac"

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

BRAND_HISENSE = "hisense_connectlife"
BRAND_DEMO = "demo"

CONF_BRAND = "brand"
CONF_TOKEN = "token"  # noqa: S105
CONF_SOURCE_ID = "source_id"
CONF_REDIRECT_URI = "redirect_uri"
CONF_ACCOUNT_ID = "account_id"
CONF_DEMO_ZONES = "demo_zones"
CONF_DEMO_SPEED = "demo_speed"

SUBENTRY_ZONE = "zone"
CONF_UNIT_ID = "unit_id"
CONF_ZONE = "zone"
CONF_AREA = "area"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 30

# Writes made within this window are merged into one cloud call.
COALESCE_SECONDS = 0.3
# Commanded values win over polled ones until confirmed or this long has passed.
OVERLAY_SECONDS = 20
# Refresh this long after a write to confirm what the unit did.
CONFIRM_REFRESH_SECONDS = 5

# Zone subentry: room sensors and zoning preferences.
CONF_TEMP_SENSOR = "temperature_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_PRESENCE_SENSORS = "presence_sensors"
CONF_WINDOW_SENSORS = "window_sensors"
CONF_OFFSET = "comfort_offset"
CONF_PRIORITY = "priority"
CONF_MIN_OPENING = "min_opening"
CONF_MAX_OPENING = "max_opening"
CONF_SPILL = "spill"
CONF_GUEST = "guest_zone"
CONF_SLEEP_ZONE = "sleep_zone"
CONF_DOOR_SENSORS = "door_sensors"
CONF_PRESENCE_HOLD = "presence_hold"
DEFAULT_PRESENCE_HOLD = 15

# Options: smart comfort inputs and targets.
CONF_OUTDOOR_SENSOR = "outdoor_sensor"
CONF_WEATHER = "weather_entity"
CONF_PEOPLE = "people"
CONF_SOLAR_SENSOR = "solar_grid_sensor"
CONF_SOLAR_EXPORT_NEGATIVE = "solar_export_negative"
CONF_HEAT_DAY = "heat_day"
CONF_HEAT_NIGHT = "heat_night"
CONF_HEAT_AWAY = "heat_away"
CONF_COOL_DAY = "cool_day"
CONF_COOL_NIGHT = "cool_night"
CONF_COOL_AWAY = "cool_away"
CONF_HUMIDITY_MAX = "humidity_max"
CONF_NIGHT_START = "night_start"
CONF_NIGHT_END = "night_end"
CONF_WINDOW_DELAY = "window_delay"
CONF_DOOR_DELAY = "door_delay"
CONF_MIN_ZONES = "min_open_zones"
CONF_MIN_TOTAL = "min_total_opening"
DEFAULT_WINDOW_DELAY = 2
DEFAULT_DOOR_DELAY = 5
DEFAULT_SLEEP_TARGET = 22.0

# How often advice is re-evaluated, and how long a dismissal lasts by default.
ADVICE_INTERVAL_SECONDS = 60
FORECAST_REFRESH_MINUTES = 30
DEFAULT_DISMISS_MINUTES = 240
WS_SCHEMA_VERSION = 1
