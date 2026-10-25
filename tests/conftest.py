"""Pure tests: no Home Assistant needed.

The integration package ``__init__.py`` imports Home Assistant, so a stub
package is registered that points at the integration folder. Submodules then
import normally while ``__init__.py`` never runs.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "cyborg_hvac"
FIXTURES = Path(__file__).parent / "fixtures"

if "cyborg_hvac" not in sys.modules:
    stub = types.ModuleType("cyborg_hvac")
    stub.__path__ = [str(PKG)]
    sys.modules["cyborg_hvac"] = stub


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def hacz01():
    """Device list entry, property list and static data for an HACZ01."""
    return load_fixture("hisense_009_19901.json")
