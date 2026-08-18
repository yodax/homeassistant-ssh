"""A sensor command with no scan interval runs once and then never again.

HA arms no refresh timer when a coordinator's update_interval is None, and the
manager's periodic update skips any command that already produced output. The
entity stays available and keeps showing its startup value forever, which turns
a health check into a signal that reads healthy no matter what. Nothing said so
in the UI or the log; this warning is the fix.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from ssh_terminal_manager import SensorKey

from custom_components.ssh import async_warn_about_never_refreshing_commands


def command(*keys: str, interval: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        interval=interval,
        sensors=[SimpleNamespace(key=key) for key in keys],
    )


def manager(*commands: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(name="pve", sensor_commands=list(commands))


def warnings_from(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]


def test_warns_for_a_custom_command_without_interval(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real case: a ZFS pool health check that silently never re-runs."""
    with caplog.at_level(logging.WARNING):
        async_warn_about_never_refreshing_commands(manager(command("zfs_pool_status")))

    assert any("zfs_pool_status" in message for message in warnings_from(caplog))


def test_does_not_warn_for_static_device_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """uname / dmidecode / cpuinfo genuinely never change - no noise for them."""
    with caplog.at_level(logging.WARNING):
        async_warn_about_never_refreshing_commands(
            manager(
                command(SensorKey.OS_NAME),
                command(SensorKey.MACHINE_TYPE),
                command(SensorKey.HOSTNAME),
                command(SensorKey.MAC_ADDRESS),
                command(SensorKey.TOTAL_MEMORY),
                command(
                    SensorKey.DEVICE_NAME,
                    SensorKey.DEVICE_MODEL,
                    SensorKey.MANUFACTURER,
                    SensorKey.SERIAL_NUMBER,
                ),
                command(SensorKey.CPU_NAME, SensorKey.CPU_CORES),
            )
        )

    assert warnings_from(caplog) == []


def test_does_not_warn_when_interval_is_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        async_warn_about_never_refreshing_commands(
            manager(command("kopia_status", interval=300))
        )

    assert warnings_from(caplog) == []


def test_warns_for_a_mixed_command(caplog: pytest.LogCaptureFixture) -> None:
    """A command mixing a static key with a changing one still refreshes never."""
    with caplog.at_level(logging.WARNING):
        async_warn_about_never_refreshing_commands(
            manager(command(SensorKey.OS_NAME, "disk_usage"))
        )

    assert any("disk_usage" in message for message in warnings_from(caplog))
