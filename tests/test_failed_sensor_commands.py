"""A sensor command that exits non-zero must not read as a healthy sensor.

terminal_manager treats any command that ran as a success whatever its exit code
(`command.error` stays None) and then clears every sensor value that command
feeds. So a health check that starts failing blanks its sensors to `unknown`
while ssh.poll_sensor reports success and nothing above DEBUG is logged - the
exact shape of the ZFS dead-signal problem, and the reason these tests exist.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from custom_components.ssh import get_poll_result
from custom_components.ssh.coordinator import (
    get_failed_exit_code,
    log_failed_exit_code,
)


def make_command(code: int | None, stderr: list[str] | None = None):
    """A command whose last run exited with `code`, or never ran if None."""
    output = (
        None
        if code is None
        else SimpleNamespace(code=code, stderr=stderr or [], stdout=[])
    )
    return SimpleNamespace(
        output=output, sensors=[SimpleNamespace(key="smart_status")]
    )


def entry_data_for(command):
    return SimpleNamespace(
        manager=SimpleNamespace(get_sensor_command=lambda key: command)
    )


ENTITY = SimpleNamespace(
    entity_id="sensor.pve_smart_status", name="SMART status", key="smart_status"
)


def test_zero_exit_is_not_a_failure() -> None:
    assert get_failed_exit_code(make_command(0)) is None


def test_non_zero_exit_is_a_failure() -> None:
    assert get_failed_exit_code(make_command(1)) == 1


def test_command_that_never_ran_is_not_a_failure() -> None:
    """Guard against flagging a command at startup before its first run."""
    assert get_failed_exit_code(make_command(None)) is None


def test_poll_reports_failure_for_non_zero_exit() -> None:
    """The core fix: the poll no longer claims success for a blanked sensor."""
    result = get_poll_result(
        entry_data_for(make_command(1, ["smartctl: device open failed"])),
        ENTITY,
        None,
    )

    assert result["success"] is False
    assert result["code"] == 1
    assert "device open failed" in result["error"]


def test_poll_reports_success_for_healthy_command() -> None:
    """The false-positive guard.

    This is what keeps the fix from crying wolf across ~110 live sensor
    commands: a command that exits 0 stays a success even if its stdout was
    empty.
    """
    result = get_poll_result(entry_data_for(make_command(0)), ENTITY, None)

    assert result["success"] is True
    assert "error" not in result


def test_transport_error_still_wins() -> None:
    """A real connection error keeps its own message rather than being masked."""
    result = get_poll_result(
        entry_data_for(make_command(0)), ENTITY, OSError("connection reset")
    )

    assert result["success"] is False
    assert "connection reset" in result["error"]


def test_unknown_sensor_key_does_not_crash() -> None:
    """A dynamic sensor whose command has gone away must not break the poll."""

    def raise_key_error(key):
        raise KeyError(key)

    entry_data = SimpleNamespace(
        manager=SimpleNamespace(get_sensor_command=raise_key_error)
    )

    assert get_poll_result(entry_data, ENTITY, None)["success"] is True


def test_failure_is_logged_once_then_recovery_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One line per outage, not one per poll - and one when it comes back.

    A 300s command that logged every cycle would produce ~290 lines a day per
    broken command, which is how a warning stops being read.
    """
    logger = logging.getLogger("test.ssh")
    command = make_command(1, ["boom"])

    with caplog.at_level(logging.WARNING, logger="test.ssh"):
        log_failed_exit_code(command, "pve", logger)
        log_failed_exit_code(command, "pve", logger)
        log_failed_exit_code(command, "pve", logger)

    failures = [r for r in caplog.records if "exited with code" in r.getMessage()]
    assert len(failures) == 1
    assert "smart_status" in failures[0].getMessage()

    command.output = SimpleNamespace(code=0, stderr=[], stdout=[])
    with caplog.at_level(logging.WARNING, logger="test.ssh"):
        log_failed_exit_code(command, "pve", logger)
        log_failed_exit_code(command, "pve", logger)

    recoveries = [r for r in caplog.records if "recovered" in r.getMessage()]
    assert len(recoveries) == 1


def test_healthy_command_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """The no-noise guard: a command that has never failed stays silent."""
    logger = logging.getLogger("test.ssh")

    with caplog.at_level(logging.WARNING, logger="test.ssh"):
        log_failed_exit_code(make_command(0), "pve", logger)

    assert caplog.records == []
