"""SSH terminal that tolerates command output which is not valid UTF-8."""

from __future__ import annotations

import time

from ssh_terminal_manager import CommandOutput, SSHTerminal
from terminal_manager import ExecutionError


def _decode_lines(data: bytes) -> list[str]:
    """Split raw output into lines the way paramiko's line iterator does.

    paramiko splits on "\\n" and the library then collapses whatever other line
    breaks a line still holds ("".join(line.splitlines())). Undecodable bytes
    become U+FFFD instead of raising.
    """
    if not data:
        return []
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return ["".join(line.splitlines()) for line in lines]


class TolerantSSHTerminal(SSHTerminal):
    """SSHTerminal whose non-shell execution survives invalid UTF-8 output.

    Upstream iterates paramiko's text-mode ChannelFile, which decodes each line
    as strict UTF-8. One bad byte - e.g. `cut -c` splitting a multi-byte
    character - raised UnicodeDecodeError, which the library wraps in
    ExecutionError, and the manager answers every ExecutionError by dropping
    the connection. A single sensor command doing that on every poll left the
    entry disconnected between polls, so every `ssh.run_action` against it
    failed with "Not connected" (2026-10-01, devbox).
    """

    def _execute_without_shell(self, string: str, timeout: int) -> CommandOutput:
        try:
            stdin, stdout, stderr = self._client.exec_command(
                string,
                timeout=float(timeout),
            )
        except Exception as exc:
            raise ExecutionError(f"Failed to execute command: {exc}") from exc

        try:
            return CommandOutput(
                string,
                time.time(),
                _decode_lines(stdout.read()),
                _decode_lines(stderr.read()),
                stdout.channel.recv_exit_status(),
            )
        except TimeoutError:
            stdin.channel.close()
            raise
        except Exception as exc:
            raise ExecutionError(f"Failed to read command output: {exc}") from exc
