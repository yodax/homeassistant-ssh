"""Tests for how command failures are surfaced.

Upstream reported ``success: True`` for any command that ran, whatever its exit
code, and then discarded the whole result set whenever the caller did not ask
for a response. An action button wired to a script that errored out was
indistinguishable from one that worked.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.core import HomeAssistant, HomeAssistantError
from homeassistant.helpers import device_registry as dr

from custom_components.ssh import async_register_services
from custom_components.ssh.const import DOMAIN, SERVICE_EXECUTE_COMMAND

from .helpers import add_config_entry


def make_entry_data(device_entry, code: int = 0, stderr: list[str] | None = None):
    output = SimpleNamespace(
        command_string="/usr/local/bin/deploy.sh",
        stdout=[],
        stderr=stderr if stderr is not None else [],
        code=code,
    )
    manager = SimpleNamespace(async_execute_command=AsyncMock(return_value=output))
    return SimpleNamespace(manager=manager, device_entry=device_entry)


@pytest.fixture
def ssh_device(hass: HomeAssistant):
    entry = add_config_entry(hass, DOMAIN)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "test-host")},
        name="test-host",
    )
    return entry, device


async def call_execute(hass: HomeAssistant, device, **kwargs):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_EXECUTE_COMMAND,
        {"command": "/usr/local/bin/deploy.sh", "device_id": [device.id]},
        blocking=True,
        **kwargs,
    )


async def test_non_zero_exit_code_is_reported_as_failure(
    hass: HomeAssistant, ssh_device
) -> None:
    """A command that runs but exits 1 must not report success."""
    entry, device = ssh_device
    hass.data[DOMAIN] = {
        entry.entry_id: make_entry_data(device, code=1, stderr=["no such file"])
    }
    async_register_services(hass, DOMAIN)

    response = await call_execute(hass, device, return_response=True)

    result = response["results"][0]
    assert result["success"] is False
    assert result["code"] == 1
    assert "no such file" in result["error"]


async def test_zero_exit_code_is_still_success(hass: HomeAssistant, ssh_device) -> None:
    """The happy path is unchanged."""
    entry, device = ssh_device
    hass.data[DOMAIN] = {entry.entry_id: make_entry_data(device, code=0)}
    async_register_services(hass, DOMAIN)

    response = await call_execute(hass, device, return_response=True)

    assert response["results"][0]["success"] is True
    assert "error" not in response["results"][0]


async def test_failure_without_response_variable_raises(
    hass: HomeAssistant, ssh_device
) -> None:
    """The silent-failure path.

    Our scripts call ssh.run_action with no response_variable. Upstream threw
    the failure away and the calling script carried on as if the remote command
    had run.
    """
    entry, device = ssh_device
    hass.data[DOMAIN] = {
        entry.entry_id: make_entry_data(device, code=127, stderr=["command not found"])
    }
    async_register_services(hass, DOMAIN)

    with pytest.raises(HomeAssistantError, match="command not found"):
        await call_execute(hass, device)


async def test_failure_is_logged(
    hass: HomeAssistant, ssh_device, caplog: pytest.LogCaptureFixture
) -> None:
    """A failure must leave a trace at ERROR, not only at DEBUG."""
    entry, device = ssh_device
    hass.data[DOMAIN] = {
        entry.entry_id: make_entry_data(device, code=2, stderr=["permission denied"])
    }
    async_register_services(hass, DOMAIN)

    with caplog.at_level(logging.ERROR), pytest.raises(HomeAssistantError):
        await call_execute(hass, device)

    assert any(
        "permission denied" in record.getMessage()
        and record.levelno >= logging.ERROR
        for record in caplog.records
    )


async def test_success_without_response_variable_does_not_raise(
    hass: HomeAssistant, ssh_device
) -> None:
    """Guard against over-raising: a successful fire-and-forget call is fine."""
    entry, device = ssh_device
    hass.data[DOMAIN] = {entry.entry_id: make_entry_data(device, code=0)}
    async_register_services(hass, DOMAIN)

    assert await call_execute(hass, device) is None
