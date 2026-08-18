"""Every module must import cleanly against the pinned Home Assistant version.

Cheap, and it catches the whole class of "symbol moved between HA modules"
breakage that would otherwise only show up as a platform silently failing to set
up on ct100. It already caught a real one: an edit that moved TextMode onto the
wrong import line, which would have taken out the text platform on deploy.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "ssh"

MODULES = sorted(
    path.stem for path in COMPONENT_DIR.glob("*.py") if path.stem != "__init__"
)


def test_module_list_is_not_empty() -> None:
    """Guard the guard - an empty list would make the check below vacuous."""
    assert len(MODULES) >= 15


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module: str) -> None:
    importlib.import_module(f"custom_components.ssh.{module}")


def test_package_imports() -> None:
    importlib.import_module("custom_components.ssh")
