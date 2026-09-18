"""Pure readers of tunnel state: files in the state dir, process liveness, routes.

Nothing here changes system state. Everything takes the state directory and a
VPN id so tests can point it at a temp folder.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

PIN_PATTERN = re.compile(r"pin-sha256:[A-Za-z0-9+/=]+")
TIMESTAMP_PATTERN = re.compile(r"^\[[^\]]*\]\s*")

# Checked in this order; the first pattern with a match wins, scanning from the
# end of the log. Specific messages beat generic ones.
FAILURE_PATTERNS = (
    re.compile(r"vpnconnect: .*"),
    re.compile(r"Login failed"),
    re.compile(r"failed verification"),
    re.compile(r"Script '.*' returned error \d+"),
    re.compile(r"Failed to .*"),
    re.compile(r"sudo: .*"),
)

# openconnect discards the connect script's exit code: on a refusal it leaves
# the tun device up with no address or routes, backgrounds and writes the pid
# file. No interface can appear after one of these lines, so waiting is futile.
SCRIPT_FAILURE_PATTERNS = (
    re.compile(r"vpnconnect: .*"),
    re.compile(r"Script '.*' returned error \d+"),
)

# Any of these in a probe's output proves the TLS session reached the server.
REACHABLE_PATTERNS = (
    re.compile(r"Connected to HTTPS on"),
    re.compile(r"Got HTTP response"),
    re.compile(r"Login failed"),
    re.compile(r"WebVPN cookie"),
)


def pid_path(state_dir: Path, vpn_id: str) -> Path:
    return Path(state_dir) / f"{vpn_id}.pid"


def log_path(state_dir: Path, vpn_id: str) -> Path:
    return Path(state_dir) / f"{vpn_id}.log"


def iface_path(state_dir: Path, vpn_id: str) -> Path:
    return Path(state_dir) / f"{vpn_id}.iface"


def read_pid(state_dir: Path, vpn_id: str) -> int | None:
    try:
        text = pid_path(state_dir, vpn_id).read_text().strip()
    except FileNotFoundError:
        return None
    return int(text) if text.isdigit() else None


def pid_alive(pid: int) -> bool:
    """True when a process with this pid exists.

    openconnect runs as root, so signalling it from the app's user fails with
    EPERM. That still proves the process exists.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_iface(state_dir: Path, vpn_id: str) -> tuple[str, str] | None:
    """(interface, ip) written by scripts/vpnc-split.sh, or None."""
    try:
        parts = iface_path(state_dir, vpn_id).read_text().split()
    except FileNotFoundError:
        return None
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def read_log_tail(state_dir: Path, vpn_id: str, lines: int = 50) -> list[str]:
    try:
        text = log_path(state_dir, vpn_id).read_text(errors="replace")
    except FileNotFoundError:
        return []
    non_empty = [line for line in text.splitlines() if line.strip()]
    return non_empty[-lines:]


def strip_timestamp(line: str) -> str:
    """Remove the '[2026-09-17 14:00:01] ' prefix added by openconnect --timestamp."""
    return TIMESTAMP_PATTERN.sub("", line).strip()


def parse_failure(log_text: str) -> str | None:
    """The most useful failure line in a log, or the last line, or None if empty."""
    lines = [line for line in log_text.splitlines() if line.strip()]
    if not lines:
        return None
    for pattern in FAILURE_PATTERNS:
        for line in reversed(lines):
            if pattern.search(line):
                return strip_timestamp(line)
    return strip_timestamp(lines[-1])


def script_failure(log_text: str) -> str | None:
    """The connect script's own refusal or error line in a log, or None."""
    lines = [line for line in log_text.splitlines() if line.strip()]
    for pattern in SCRIPT_FAILURE_PATTERNS:
        for line in reversed(lines):
            if pattern.search(line):
                return strip_timestamp(line)
    return None


def pin_from_probe(output: str) -> str | None:
    match = PIN_PATTERN.search(output)
    return match.group(0) if match else None


def server_reachable(output: str) -> bool:
    return any(pattern.search(output) for pattern in REACHABLE_PATTERNS)


def count_routes(interface: str, netstat_output: str | None = None) -> int:
    """Number of IPv4 routes bound to an interface, from `netstat -rn -f inet`.

    Columns are: Destination Gateway Flags Netif [Expire]; Netif is column 4.
    """
    if netstat_output is None:
        try:
            netstat_output = subprocess.run(
                ["netstat", "-rn", "-f", "inet"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
        except OSError:
            return 0
    count = 0
    for line in netstat_output.splitlines():
        columns = line.split()
        if len(columns) >= 4 and columns[3] == interface:
            count += 1
    return count
