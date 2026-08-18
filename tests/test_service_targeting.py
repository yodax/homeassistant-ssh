"""Tests for resolving which config entries a service call targets.

Upstream indexed ``hass.data[domain]`` directly with every config entry id
attached to the targeted device. A device can carry entries belonging to other
integrations - a template helper assigned to the SSH device for organisation is
the common case - which raised a bare ``KeyError`` and killed the service call
(upstream issue #83).
"""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant, ServiceValidationError

from custom_components.ssh import get_targeted_entry_data
from custom_components.ssh.const import DOMAIN

from .helpers import add_config_entry

SSH_ENTRY_DATA = object()
OTHER_ENTRY_DATA = object()


async def test_returns_entry_data_for_ssh_entry(hass: HomeAssistant) -> None:
    """A plain SSH target resolves to its entry data."""
    entry = add_config_entry(hass, DOMAIN)
    hass.data[DOMAIN] = {entry.entry_id: SSH_ENTRY_DATA}

    assert get_targeted_entry_data(hass, DOMAIN, {entry.entry_id}) == [SSH_ENTRY_DATA]


async def test_ignores_config_entries_of_other_integrations(
    hass: HomeAssistant,
) -> None:
    """Issue #83: a template helper on the SSH device must not raise KeyError.

    This is the regression guard - before the fix this call raised
    ``KeyError`` and every ssh.execute_command / ssh.run_action against that
    device failed.
    """
    ssh_entry = add_config_entry(hass, DOMAIN)
    template_entry = add_config_entry(hass, "template")
    hass.data[DOMAIN] = {ssh_entry.entry_id: SSH_ENTRY_DATA}

    result = get_targeted_entry_data(
        hass, DOMAIN, {ssh_entry.entry_id, template_entry.entry_id}
    )

    assert result == [SSH_ENTRY_DATA]


async def test_resolves_multiple_ssh_entries(hass: HomeAssistant) -> None:
    """Targeting two SSH hosts returns both, ignoring a foreign entry."""
    first = add_config_entry(hass, DOMAIN)
    second = add_config_entry(hass, DOMAIN)
    template_entry = add_config_entry(hass, "template")
    hass.data[DOMAIN] = {
        first.entry_id: SSH_ENTRY_DATA,
        second.entry_id: OTHER_ENTRY_DATA,
    }

    result = get_targeted_entry_data(
        hass, DOMAIN, {first.entry_id, second.entry_id, template_entry.entry_id}
    )

    assert sorted(map(id, result)) == sorted([id(SSH_ENTRY_DATA), id(OTHER_ENTRY_DATA)])


async def test_raises_when_ssh_entry_is_not_loaded(hass: HomeAssistant) -> None:
    """An SSH entry that exists but failed to set up gets a clear error.

    Silently returning no results here would let an automation believe its
    remote command ran.
    """
    entry = add_config_entry(hass, DOMAIN)
    hass.data[DOMAIN] = {}

    with pytest.raises(ServiceValidationError, match="not loaded"):
        get_targeted_entry_data(hass, DOMAIN, {entry.entry_id})


async def test_raises_when_target_has_no_ssh_entry(hass: HomeAssistant) -> None:
    """Targeting a device with no SSH entry at all fails loudly, not silently."""
    template_entry = add_config_entry(hass, "template")
    hass.data[DOMAIN] = {}

    with pytest.raises(ServiceValidationError, match="No loaded ssh config entry"):
        get_targeted_entry_data(hass, DOMAIN, {template_entry.entry_id})


async def test_raises_on_empty_target(hass: HomeAssistant) -> None:
    """No resolved entries at all is an error rather than a silent no-op."""
    hass.data[DOMAIN] = {}

    with pytest.raises(ServiceValidationError):
        get_targeted_entry_data(hass, DOMAIN, set())


async def test_missing_domain_data_does_not_raise_key_error(
    hass: HomeAssistant,
) -> None:
    """hass.data has no ssh key at all (no entry ever set up)."""
    entry = add_config_entry(hass, DOMAIN)

    with pytest.raises(ServiceValidationError):
        get_targeted_entry_data(hass, DOMAIN, {entry.entry_id})
