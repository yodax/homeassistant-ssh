"""Static guard against passing `hass` to Home Assistant service helpers.

The runtime guard in conftest only catches call sites a test actually
exercises. This walks the source instead, so a helper called from an untested
code path cannot quietly reintroduce the deprecation and resurface as a wall of
log warnings - and, from HA Core 2026.10, as a TypeError.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from homeassistant.helpers import service as ha_service

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "ssh"

# Helpers in homeassistant.helpers.service that dropped their leading `hass`
# argument. Derived from HA itself so the list cannot drift out of date: the
# decorator wraps the real function, leaving __wrapped__ behind.
DEPRECATED_HASS_HELPERS = {
    name
    for name in dir(ha_service)
    if not name.startswith("_")
    and callable(getattr(ha_service, name, None))
    and hasattr(getattr(ha_service, name), "__wrapped__")
}


def source_files() -> list[Path]:
    return sorted(COMPONENT_DIR.rglob("*.py"))


def test_helper_list_is_not_empty() -> None:
    """Guard the guard: an empty set would make every check below vacuous."""
    assert "async_extract_config_entry_ids" in DEPRECATED_HASS_HELPERS
    assert "async_extract_entities" in DEPRECATED_HASS_HELPERS


@pytest.mark.parametrize("path", source_files(), ids=lambda p: p.name)
def test_no_hass_passed_to_deprecated_helpers(path: Path) -> None:
    """No call in the integration may pass `hass` to these helpers."""
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name not in DEPRECATED_HASS_HELPERS:
            continue

        passes_hass = any(
            isinstance(arg, ast.Name) and arg.id == "hass" for arg in node.args[:1]
        ) or any(kw.arg == "hass" for kw in node.keywords)

        if passes_hass:
            offenders.append(f"{path.name}:{node.lineno} {name}(hass, ...)")

    assert not offenders, (
        "These calls pass the deprecated `hass` argument, removed in HA Core "
        "2026.10:\n" + "\n".join(offenders)
    )
