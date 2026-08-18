"""The service schemas must accept every target the UI can produce.

services.yaml declares a full target selector, so the frontend can send
area_id / floor_id / label_id. The schemas were plain vol.Schema, which
defaults to PREVENT_EXTRA and rejected those with "extra keys not allowed",
making the area picker in the service dialog fail outright.
"""

from __future__ import annotations

import pytest
import voluptuous as vol

from homeassistant.const import (
    ATTR_AREA_ID,
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    ATTR_FLOOR_ID,
    ATTR_LABEL_ID,
)

from custom_components.ssh import (
    EXECUTE_COMMAND_SCHEMA,
    RUN_ACTION_SCHEMA,
    SET_VALUE_SCHEMA,
)

TARGET_KEYS = [ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID, ATTR_FLOOR_ID, ATTR_LABEL_ID]


@pytest.mark.parametrize("target_key", TARGET_KEYS)
def test_execute_command_accepts_every_target_type(target_key: str) -> None:
    EXECUTE_COMMAND_SCHEMA({"command": "uptime", target_key: ["some-id"]})


@pytest.mark.parametrize("target_key", TARGET_KEYS)
def test_run_action_accepts_every_target_type(target_key: str) -> None:
    RUN_ACTION_SCHEMA({"key": "restart", target_key: ["some-id"]})


@pytest.mark.parametrize("target_key", TARGET_KEYS)
def test_execute_command_accepts_bare_string_target(target_key: str) -> None:
    """The frontend sends a bare string for a single target, not a list."""
    EXECUTE_COMMAND_SCHEMA({"command": "uptime", target_key: "some-id"})


def test_set_value_still_requires_entity_id() -> None:
    """set_value is entity-scoped by design; that constraint must survive."""
    SET_VALUE_SCHEMA({"values": ["1"], ATTR_ENTITY_ID: ["text.foo"]})

    with pytest.raises(vol.Invalid):
        SET_VALUE_SCHEMA({"values": ["1"]})


def test_required_fields_are_still_enforced() -> None:
    """Guard against loosening the schemas into accepting anything."""
    with pytest.raises(vol.Invalid):
        EXECUTE_COMMAND_SCHEMA({ATTR_DEVICE_ID: ["x"]})  # no command

    with pytest.raises(vol.Invalid):
        RUN_ACTION_SCHEMA({ATTR_DEVICE_ID: ["x"]})  # no key
