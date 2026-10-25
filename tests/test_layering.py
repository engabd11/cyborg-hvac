"""The core and brand backends stay free of Home Assistant imports."""

from pathlib import Path
import re

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "cyborg_hvac"
HA_IMPORT = re.compile(r"^\s*(from|import)\s+homeassistant\b", re.MULTILINE)


def pure_modules():
    for folder in ("core", "backends"):
        for path in (PKG / folder).rglob("*.py"):
            if path.name != "adapter.py":
                yield path


def test_core_and_backends_do_not_import_home_assistant():
    offenders = [str(p.relative_to(PKG)) for p in pure_modules() if HA_IMPORT.search(p.read_text())]
    assert offenders == []


def test_strings_match_english_translation():
    strings = (PKG / "strings.json").read_text(encoding="utf-8")
    english = (PKG / "translations" / "en.json").read_text(encoding="utf-8")
    assert strings == english
