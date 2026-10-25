"""Brand registry: maps a config entry's brand to its backend adapter.

To add a brand, create ``backends/<brand>/`` with a pure backend and an
``adapter.py`` that exposes ``async_create_backend`` and
``async_create_flow_backend``, then add it here and to the config flow.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .backends.demo import adapter as demo_adapter
from .backends.hisense_connectlife import adapter as hisense_adapter
from .const import BRAND_DEMO, BRAND_HISENSE, CONF_BRAND
from .core.backend import HvacBackend


@dataclass(frozen=True, slots=True)
class BrandAdapter:
    """How Home Assistant builds a backend for one brand."""

    key: str
    create_backend: Callable[[HomeAssistant, ConfigEntry], Awaitable[HvacBackend]]
    create_flow_backend: Callable[[HomeAssistant, dict[str, Any]], Awaitable[HvacBackend]]


BRANDS: dict[str, BrandAdapter] = {
    BRAND_HISENSE: BrandAdapter(
        BRAND_HISENSE,
        hisense_adapter.async_create_backend,
        hisense_adapter.async_create_flow_backend,
    ),
    BRAND_DEMO: BrandAdapter(
        BRAND_DEMO,
        demo_adapter.async_create_backend,
        demo_adapter.async_create_flow_backend,
    ),
}


def get_brand(entry_data: dict[str, Any] | Any) -> BrandAdapter:
    """Return the adapter for a config entry's data."""
    return BRANDS[entry_data[CONF_BRAND]]
