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
