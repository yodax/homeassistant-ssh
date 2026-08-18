"""Targeting an unreachable host must not look like success.

HA's async_extract_entities silently drops unavailable entities. Every entity of
an SSH entry is unavailable while its host is unreachable, so ssh.poll_sensor
against a host that is down resolved to an empty selection and returned an empty
result set - indistinguishable from a poll that worked. ssh.set_value was worse:
it pairs values with entities by index, so dropping one from the middle shifted
every later value onto the wrong sensor.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from homeassistant.core import HomeAssistant, ServiceCall, ServiceValidationError

from custom_components.ssh import get_targeted_entities, get_unavailable_results
from custom_components.ssh.const import DOMAIN, SERVICE_POLL_SENSOR


class FakeEntity:
    """Minimal stand-in for a BaseSensorEntity."""

    def __init__(self, entity_id: str, available: bool) -> None:
        self.entity_id = entity_id
        self.name = entity_id.split(".")[-1]
        self.available = available
        self.key = self.name


def call_for(hass: HomeAssistant, entity_ids: list[str] | str) -> ServiceCall:
    return ServiceCall(
        hass, DOMAIN, SERVICE_POLL_SENSOR, {"entity_id": entity_ids}
    )


async def test_unavailable_entities_are_returned_not_dropped(
    hass: HomeAssistant,
) -> None:
    """The core of the fix: the caller can still see what it could not reach."""
    up = FakeEntity("sensor.pve_processes", available=True)
    down = FakeEntity("sensor.jaguar_processes", available=False)

    available, unavailable = get_targeted_entities(
        hass, [up, down], call_for(hass, [up.entity_id, down.entity_id])
    )

    assert available == [up]
    assert unavailable == [down]


async def test_all_targets_unavailable_is_not_an_empty_success(
    hass: HomeAssistant,
) -> None:
    """A host that is entirely down must produce failure rows, not silence."""
    down = FakeEntity("sensor.jaguar_processes", available=False)

    available, unavailable = get_targeted_entities(
        hass, [down], call_for(hass, [down.entity_id])
    )

    assert available == []
    results = get_unavailable_results(unavailable)
    assert len(results) == 1
    assert results[0]["success"] is False
    assert results[0]["entity_id"] == down.entity_id
    assert "unavailable" in results[0]["error"]


async def test_untargeted_entities_are_ignored(hass: HomeAssistant) -> None:
    """Guard against the split returning everything regardless of the target."""
    wanted = FakeEntity("sensor.pve_processes", available=True)
    other = FakeEntity("sensor.devbox_processes", available=True)

    available, unavailable = get_targeted_entities(
        hass, [wanted, other], call_for(hass, [wanted.entity_id])
    )

    assert available == [wanted]
    assert unavailable == []


async def test_entity_match_all_selects_everything(hass: HomeAssistant) -> None:
    up = FakeEntity("sensor.pve_processes", available=True)
    down = FakeEntity("sensor.jaguar_processes", available=False)

    available, unavailable = get_targeted_entities(
        hass, [up, down], call_for(hass, "all")
    )

    assert available == [up]
    assert unavailable == [down]


def test_no_unavailable_entities_produces_no_rows() -> None:
    assert get_unavailable_results([]) == []
