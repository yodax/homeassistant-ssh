"""Command output that is not valid UTF-8 must not cost the entry its connection.

2026-10-01, devbox: a sensor command shortened commit subjects with `cut -c1-55`,
which counts bytes and split an em dash in half. Upstream decodes each output
line as strict UTF-8, the UnicodeDecodeError became an ExecutionError, and the
manager answers every ExecutionError by disconnecting. That happened on every
poll, so between polls the entry was disconnected and every dashboard button
(`ssh.run_action`) failed with "Execution failed (Not connected)".

These tests build the terminal through the integration's real setup path
(`async_setup_entry`), feed it the exact bytes from the incident through real
paramiko file objects, and then press a button the way the dashboard does.

Proven to fail against the pre-fix code (2026-10-01): with the fix's changes to
`__init__.py` and `config_flow.py` stashed, so `async_setup_entry` constructs
upstream's `SSHTerminal` again, `test_action_after_invalid_utf8_poll_still_runs`
fails with "Execution failed (Not connected)" - the incident's own error text -
and `test_invalid_utf8_output_is_replaced_not_fatal` with the UnicodeDecodeError
wrapped as "Failed to read command output".
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from paramiko.file import BufferedFile
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.ssh import async_setup_entry
from custom_components.ssh.const import DOMAIN
from custom_components.ssh.terminal import _decode_lines

# The bytes devbox actually produced: "kanon —" cut after the em dash's 2nd byte.
BAD_OUTPUT = b"efdcdfca :: 46 minutes ago :: Merge brief/255: kanon \xe2\x80\nb1ba5498\n"

SENSOR_COMMAND = "git log | cut -c1-55"
ACTION_COMMAND = "/usr/local/bin/tmux-session-start.sh"


class BytesChannelFile(BufferedFile):
    """A real paramiko BufferedFile in text mode, serving fixed bytes."""

    def __init__(self, data: bytes, code: int = 0) -> None:
        super().__init__()
        self._set_mode("r")
        self._data = data
        self.channel = SimpleNamespace(
            recv_exit_status=lambda: code, close=lambda: None
        )

    def _read(self, size: int) -> bytes:
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk


class StubClient:
    """Stands in for paramiko.SSHClient; output depends on the command."""

    def __init__(self, outputs: dict[str, bytes]) -> None:
        self.outputs = outputs
        self.executed: list[str] = []

    def exec_command(self, string: str, timeout: float | None = None):
        self.executed.append(string)
        return (
            BytesChannelFile(b""),
            BytesChannelFile(self.outputs.get(string, b"")),
            BytesChannelFile(b""),
        )

    def close(self) -> None:
        pass


async def setup_manager(hass: HomeAssistant, outputs: dict[str, bytes]):
    """Run async_setup_entry and return its manager, connected to a stub client."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.0.2.61",
            "port": 22,
            "username": "michael",
            "key_filename": "/config/ssh/id",
            "host_keys_filename": "/config/known_hosts",
            "load_system_host_keys": False,
            "invoke_shell": False,
            "name": "devbox",
            "mac": None,
        },
        options={
            "command_timeout": 30,
            "allow_turn_off": False,
            "disconnect_mode": False,
            "update_interval": 30,
            "action_commands": [
                {"command": ACTION_COMMAND, "name": "Tmux", "key": "tmux"}
            ],
            "sensor_commands": [
                {
                    "command": SENSOR_COMMAND,
                    "scan_interval": 300,
                    "sensors": [{"type": "text", "name": "Log", "key": "log"}],
                }
            ],
        },
    )
    entry.add_to_hass(hass)

    init = AsyncMock(return_value=True)
    with (
        patch("custom_components.ssh.async_initialize_entry", init),
        patch(
            "custom_components.ssh.SSHManager.async_load_host_keys", AsyncMock()
        ),
    ):
        assert await async_setup_entry(hass, entry)

    manager = init.call_args.args[2]
    terminal = manager._terminal
    client = StubClient(outputs)
    terminal._client = client
    terminal._connect = lambda: None
    manager.state.handle_ping_success()
    await manager.async_connect()
    assert manager.state.connected
    return manager, client


async def test_invalid_utf8_output_is_replaced_not_fatal(
    hass: HomeAssistant,
) -> None:
    manager, _ = await setup_manager(hass, {SENSOR_COMMAND: BAD_OUTPUT})

    output = await manager.async_execute_command(
        manager.get_sensor_command("log")
    )

    assert output.stdout == [
        "efdcdfca :: 46 minutes ago :: Merge brief/255: kanon �",
        "b1ba5498",
    ]
    assert manager.state.connected


async def test_action_after_invalid_utf8_poll_still_runs(
    hass: HomeAssistant,
) -> None:
    manager, client = await setup_manager(hass, {SENSOR_COMMAND: BAD_OUTPUT})

    # A full sensor poll, as the coordinator runs it every update interval.
    await manager.async_execute_commands(manager.sensor_commands, raise_errors=False)

    # Then the dashboard button.
    output = await manager.async_run_action("tmux")

    assert output.code == 0
    assert client.executed[-1] == ACTION_COMMAND


def test_decode_lines_matches_paramiko_line_splitting() -> None:
    assert _decode_lines(b"") == []
    assert _decode_lines(b"a\n") == ["a"]
    assert _decode_lines(b"a\nb") == ["a", "b"]
    assert _decode_lines(b"a\n\nb\n") == ["a", "", "b"]
    assert _decode_lines(b"a\r\nb\n") == ["a", "b"]
    assert _decode_lines("café\n".encode()) == ["café"]
