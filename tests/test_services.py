"""End-to-end tests for the domain services.

These exercise the real service handlers through ``hass.services.async_call``,
which is what makes them the regression guard for the Home Assistant service
helper signatures: ``pytest.ini`` turns the "deprecated argument hass" warning
into an error, so reintroducing it fails the suite rather than quietly filling
the log until HA Core 2026.10 removes the argument entirely.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.core import HomeAssistant, ServiceCall, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.service import async_extract_config_entry_ids

from custom_components.ssh import async_register_services
from custom_components.ssh.const import DOMAIN, SERVICE_EXECUTE_COMMAND

from .helpers import add_config_entry


def make_entry_data(device_entry, stdout: list[str] | None = None) -> SimpleNamespace:
    """Build a minimal stand-in for EntryData.

    Only the attributes the service handlers touch are provided, so the tests
    stay independent of ssh_terminal_manager's internals.
    """
    output = SimpleNamespace(
        command_string="echo hi",
        stdout=stdout if stdout is not None else ["hi"],
        stderr=[],
        code=0,
    )
    manager = SimpleNamespace(async_execute_command=AsyncMock(return_value=output))
    return SimpleNamespace(manager=manager, device_entry=device_entry)


@pytest.fixture
def ssh_device(hass: HomeAssistant):
    """An SSH config entry with a device in the registry."""
    entry = add_config_entry(hass, DOMAIN)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "test-host")},
        name="test-host",
    )
    return entry, device


async def test_execute_command_returns_output(hass: HomeAssistant, ssh_device) -> None:
    """A targeted command runs and its output comes back in the response."""
    entry, device = ssh_device
    entry_data = make_entry_data(device)
    hass.data[DOMAIN] = {entry.entry_id: entry_data}
    async_register_services(hass, DOMAIN)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_EXECUTE_COMMAND,
        {"command": "echo hi", "device_id": [device.id]},
        blocking=True,
        return_response=True,
    )

    assert response["results"][0]["success"] is True
    assert response["results"][0]["stdout"] == ["hi"]
    entry_data.manager.async_execute_command.assert_awaited_once()


async def test_execute_command_ignores_foreign_config_entry(
    hass: HomeAssistant, ssh_device
) -> None:
    """Issue #83, end to end.

    A template helper assigned to the SSH device contributes its own config
    entry to the call's resolved entry ids, because HA extracts entry ids from
    the device's entities as well as from the device itself. The command must
    still run instead of raising KeyError.
    """
    entry, device = ssh_device
    template_entry = add_config_entry(hass, "template")
    er.async_get(hass).async_get_or_create(
        "switch",
        "template",
        "template-switch-unique-id",
        config_entry=template_entry,
        device_id=device.id,
    )

    # Guard the guard: if the foreign entry does not actually end up in the
    # resolved set, this test would pass without reproducing issue #83.
    call = ServiceCall(
        hass, DOMAIN, SERVICE_EXECUTE_COMMAND, {"device_id": [device.id]}
    )
    assert template_entry.entry_id in await async_extract_config_entry_ids(call)

    entry_data = make_entry_data(device)
    hass.data[DOMAIN] = {entry.entry_id: entry_data}
    async_register_services(hass, DOMAIN)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_EXECUTE_COMMAND,
        {"command": "echo hi", "device_id": [device.id]},
        blocking=True,
        return_response=True,
    )

    assert response["results"][0]["success"] is True
    entry_data.manager.async_execute_command.assert_awaited_once()


async def test_execute_command_reports_failure_without_raising(
    hass: HomeAssistant, ssh_device
) -> None:
    """A failing command reports success=False with the error, not an exception."""
    entry, device = ssh_device
    entry_data = make_entry_data(device)
    entry_data.manager.async_execute_command = AsyncMock(
        side_effect=OSError("host unreachable")
    )
    hass.data[DOMAIN] = {entry.entry_id: entry_data}
    async_register_services(hass, DOMAIN)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_EXECUTE_COMMAND,
        {"command": "echo hi", "device_id": [device.id]},
        blocking=True,
        return_response=True,
    )

    assert response["results"][0]["success"] is False
    assert "host unreachable" in response["results"][0]["error"]


async def test_execute_command_on_unloaded_entry_raises(
    hass: HomeAssistant, ssh_device
) -> None:
    """An unloaded SSH entry fails the call loudly instead of doing nothing."""
    entry, device = ssh_device
    hass.data[DOMAIN] = {}
    async_register_services(hass, DOMAIN)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXECUTE_COMMAND,
            {"command": "echo hi", "device_id": [device.id]},
            blocking=True,
            return_response=True,
        )
