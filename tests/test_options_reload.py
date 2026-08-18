"""The options flow must reload the entry exactly once.

Registering a config entry update listener *and* using
async_update_reload_and_abort / OptionsFlowWithReload reloads twice per change:
two SSH teardown/reconnect cycles and two full entity re-adds, with every sensor
unavailable throughout. HA reports the combination for removal in 2026.12 and
refuses it outright.
"""

from __future__ import annotations

import ast
from pathlib import Path

from homeassistant import config_entries

from custom_components.ssh.config_flow import OptionsFlow

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "ssh"


def test_options_flow_reloads_itself() -> None:
    assert issubclass(OptionsFlow, config_entries.OptionsFlowWithReload)


def test_no_update_listener_is_registered() -> None:
    """The half that has to go when OptionsFlowWithReload is in use.

    Asserted against the source rather than a live entry because HA only raises
    on the combination at reload time, which a unit test would not reach.
    """
    offenders = []

    for path in sorted(COMPONENT_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_update_listener"
            ):
                offenders.append(f"{path.name}:{node.lineno}")

    assert not offenders, (
        "add_update_listener cannot be combined with OptionsFlowWithReload "
        "(HA raises ValueError; removed in 2026.12): " + ", ".join(offenders)
    )
