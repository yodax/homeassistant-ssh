"""Smaller correctness fixes from the 2026-08-18 review."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import MAX_LENGTH_STATE_STATE
from homeassistant.core import HomeAssistant

from custom_components.ssh.const import CONF_STATE_CLASS
from custom_components.ssh.helpers import (
    _get_template,
    get_device_sensor_update_handler,
)
from custom_components.ssh.sensor import Entity as SensorEntity
from custom_components.ssh.text import Entity as TextEntity


class FakeNumberSensor:
    """Stands in for ssh_terminal_manager's NumberSensor."""


def make_sensor_entity(attributes: dict, sensor=None) -> SensorEntity:
    entity = SensorEntity.__new__(SensorEntity)
    entity._attributes = attributes
    entity._sensor = sensor if sensor is not None else SimpleNamespace()
    return entity


def test_text_max_matches_home_assistants_own_limit() -> None:
    """Defaulting to 100 rejected values HA itself would accept."""
    entity = TextEntity.__new__(TextEntity)
    entity._sensor = SimpleNamespace(maximum=None)

    assert entity.native_max == MAX_LENGTH_STATE_STATE == 255


def test_text_max_still_honours_an_explicit_maximum() -> None:
    entity = TextEntity.__new__(TextEntity)
    entity._sensor = SimpleNamespace(maximum=40)

    assert entity.native_max == 40


def test_state_class_can_be_configured() -> None:
    """Counter-style sensors were impossible while this was hardcoded."""
    entity = make_sensor_entity({CONF_STATE_CLASS: "total_increasing"})

    assert entity.state_class is SensorStateClass.TOTAL_INCREASING


def test_state_class_defaults_to_none_for_text_sensors() -> None:
    assert make_sensor_entity({}).state_class is None


def test_templates_are_compiled_once(hass: HomeAssistant) -> None:
    """The same command string must not be recompiled every poll."""
    _get_template.cache_clear()

    first = _get_template("echo {{ 1 + 1 }}", hass)
    second = _get_template("echo {{ 1 + 1 }}", hass)

    assert first is second
    assert _get_template.cache_info().hits == 1


def test_device_update_failure_does_not_propagate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A raising subscriber would stop the rest of that command's sensors.

    terminal_manager's Event.notify iterates subscribers with no try/except, so
    an exception here aborts every sensor after this one in the same command,
    every cycle, until the entry is reloaded.
    """
    registry = MagicMock()
    registry.async_update_device.side_effect = RuntimeError("device gone")
    entry_data = SimpleNamespace(
        device_entry=SimpleNamespace(id="dev1"),
        manager=SimpleNamespace(
            name="pve",
            sensors_by_key={},
            machine_type=None,
            cpu_cores=None,
            cpu_name=None,
            os_name=None,
            os_version=None,
            os_release=None,
            manufacturer=None,
            device_model=None,
            device_name=None,
            cpu_model=None,
            cpu_hardware=None,
        ),
        state_coordinator=SimpleNamespace(logger=logging.getLogger("test.ssh")),
    )

    handler = get_device_sensor_update_handler(None, entry_data, registry)

    with caplog.at_level(logging.WARNING, logger="test.ssh"):
        handler(SimpleNamespace(value="something"))  # must not raise

    assert any("device entry" in r.getMessage() for r in caplog.records)
