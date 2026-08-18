"""Fixtures for the SSH integration tests."""

from __future__ import annotations

import logging

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

# Home Assistant announces a deprecation like the `hass` argument on
# async_extract_config_entry_ids through the logger, not through
# warnings.warn, so `filterwarnings = error` cannot catch it. Any message
# carrying a removal deadline is a build break waiting to happen, so fail the
# test that produced it and name the deadline in the failure.
DEPRECATION_MARKERS = (
    "deprecated argument",
    "will be removed in HA Core",
    "is deprecated and will be removed",
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in every test."""
    yield


@pytest.fixture(autouse=True)
def fail_on_ha_deprecation(caplog: pytest.LogCaptureFixture):
    """Fail any test in which the integration triggers an HA deprecation.

    This is the regression guard for upstream's use of the removed-in-2026.10
    `hass` argument. It is deliberately broad: a future HA release deprecating
    something else we call should also break the suite while there is still
    time to fix it, rather than silently filling the production log.
    """
    caplog.set_level(logging.WARNING)
    yield

    # caplog.records is scoped to the phase that is currently running, which at
    # teardown is the teardown phase itself. The records from the test body
    # have to be asked for by name.
    offenders = [
        record.getMessage()
        for record in caplog.get_records("call")
        if record.levelno >= logging.WARNING
        and any(marker in record.getMessage() for marker in DEPRECATION_MARKERS)
    ]

    assert not offenders, "Home Assistant deprecation triggered:\n" + "\n".join(
        offenders
    )
