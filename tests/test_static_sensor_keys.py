"""The static/dynamic split behind the never-refreshes warning.

Getting this set wrong in either direction is bad: too small and the warning
cries wolf about uname every startup until it is ignored, too large and a real
health check stays silently dead.
"""

from __future__ import annotations

from ssh_terminal_manager import SensorKey

from custom_components.ssh import STATIC_SENSOR_KEYS

# Every stock sensor whose value can change while the host is running.
EXPECTED_DYNAMIC = {
    SensorKey.CPU_LOAD,
    SensorKey.FREE_DISK_SPACE,
    SensorKey.FREE_MEMORY,
    SensorKey.PROCESSES,
    SensorKey.TEMPERATURE,
}


def all_sensor_keys() -> set[str]:
    return {
        getattr(SensorKey, name)
        for name in dir(SensorKey)
        if name.isupper() and not name.startswith("_")
    }


def test_every_stock_key_is_classified() -> None:
    """A newly added stock key must be triaged, not silently treated as dynamic.

    If this fails after a ssh-terminal-manager upgrade, decide whether the new
    key is static and add it to one of the two sets - do not just delete this.
    """
    assert all_sensor_keys() == STATIC_SENSOR_KEYS | EXPECTED_DYNAMIC


def test_dynamic_keys_are_not_marked_static() -> None:
    """The signals worth warning about must not be exempted."""
    assert not (STATIC_SENSOR_KEYS & EXPECTED_DYNAMIC)


def test_known_static_keys_are_exempt() -> None:
    """Spot-check the ones that caused real log noise before being exempted."""
    for key in (
        SensorKey.OS_ARCHITECTURE,
        SensorKey.HOSTNAME,
        SensorKey.MAC_ADDRESS,
        SensorKey.SERIAL_NUMBER,
    ):
        assert key in STATIC_SENSOR_KEYS
