"""Home Assistant harness tests.

These need pytest-homeassistant-custom-component, which only runs on Linux
(CI, or the Docker command in docs/development.md).
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("pytest_homeassistant_custom_component")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading custom_components/ from this checkout."""
    return


@pytest.fixture
def hacz01():
    """HACZ01 fixture shared with the pure tests."""
    path = ROOT / "tests" / "fixtures" / "hisense_009_19901.json"
    return json.loads(path.read_text(encoding="utf-8"))
