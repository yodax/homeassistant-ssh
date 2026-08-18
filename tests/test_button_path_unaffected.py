"""Pressing a button entity must not go through the service-call wrappers.

The dashboard's action buttons - "Deploy naar web starten", "Homelab fix",
"Docker update + auto-fix", "Tafels editor herstarten" - are button entities,
not ssh.run_action service calls. Their press path is ButtonEntity.async_press
-> manager.async_run_action, which never touches get_response /
get_command_result.

That matters because those wrappers now raise on a non-zero exit code. Buttons
are fire-and-forget from the UI with nowhere to surface an exception, so the
distinction has to stay: this test fails if a future change routes button
presses through the service layer.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.ssh.button import Entity

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "ssh"

# Names defined in __init__.py that implement the raising service behaviour.
SERVICE_WRAPPERS = {"get_response", "get_command_result", "get_generic_result"}


def test_button_module_does_not_use_the_service_wrappers() -> None:
    """The structural guarantee, checked against the source."""
    tree = ast.parse((COMPONENT_DIR / "button.py").read_text())
    used = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }

    assert not (used & SERVICE_WRAPPERS), (
        "button.py now references the service wrappers, which raise on a "
        "non-zero exit code - a button press has nowhere to surface that"
    )


async def test_press_calls_run_action_directly_and_does_not_raise() -> None:
    """A failing action command must not blow up a button press."""
    entity = Entity.__new__(Entity)
    entity._manager = SimpleNamespace(
        async_run_action=AsyncMock(
            return_value=SimpleNamespace(
                command_string="/usr/local/bin/tafels-deploy-session.sh",
                stdout=[],
                stderr=["something went wrong"],
                code=1,
            )
        )
    )
    entity._command = SimpleNamespace(key="tafels_deploy_starten", name="x")

    await entity.async_press()

    entity._manager.async_run_action.assert_awaited_once_with(
        "tafels_deploy_starten"
    )
