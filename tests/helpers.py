"""Shared test helpers."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant


def add_config_entry(hass: HomeAssistant, domain: str) -> MockConfigEntry:
    """Register a config entry for `domain` and return it."""
    entry = MockConfigEntry(domain=domain)
    entry.add_to_hass(hass)
    return entry
