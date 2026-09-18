"""Credential lines in .env.

Only VPN_<ID>_USERNAME / VPN_<ID>_PASSWORD are touched; every other line,
comment and the order are kept. Flask loads .env once at startup, so the
running process reads credentials from the environment: after writing the file
the same values are set in (or deleted from) the given mapping, os.environ by
default, so load_registry sees the change without a restart.

Passwords are written to this file and to the environment only. They never
reach a log line, an API response or a command line.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, MutableMapping
from pathlib import Path

from app.services.registry import env_prefix_for

# Leading or trailing whitespace, a '#' or a quote would not survive
# python-dotenv's unquoted-value rules; a newline would break the line format.
NEEDS_QUOTING = re.compile(r"^\s|\s$|[#\r\n\"']")
# python-dotenv expands ${VAR} when it loads the file, whatever the quoting, so
# a value containing it would come back changed after a restart.
INTERPOLATION = "${"


class CredentialError(ValueError):
    """A credential cannot be stored in .env without being changed."""


def env_var_names(vpn_id: str) -> tuple[str, str]:
    """The username and password variable names for one VPN id."""
    prefix = env_prefix_for(vpn_id)
    return f"{prefix}_USERNAME", f"{prefix}_PASSWORD"


def set_credentials(
    path: str | Path,
    vpn_id: str,
    *,
    username: str | None = None,
    password: str | None = None,
    env: MutableMapping[str, str] | None = None,
) -> None:
    """Store credentials for one VPN. None keeps the current value."""
    username_var, password_var = env_var_names(vpn_id)
    values = {
        name: value
        for name, value in ((username_var, username), (password_var, password))
        if value is not None
    }
    if not values:
        return
    for name, value in values.items():
        if INTERPOLATION in value:
            raise CredentialError(f"{name} must not contain {INTERPOLATION!r}")
    _rewrite(path, values)
    target = os.environ if env is None else env
    target.update(values)


def scaffold_credentials(
    path: str | Path,
    vpn_id: str,
    *,
    username: str | None = None,
    password: str | None = None,
    env: MutableMapping[str, str] | None = None,
) -> None:
    """Store the credentials of a new VPN.

    Supplied values are written; a line that is missing is added empty so .env
    lists what the VPN needs, but a value already put there by hand is kept.
    """
    username_var, password_var = env_var_names(vpn_id)
    present = _present(path, (username_var, password_var))
    set_credentials(
        path,
        vpn_id,
        username=username or (None if username_var in present else ""),
        password=password or (None if password_var in present else ""),
        env=env,
    )


def remove_credentials(
    path: str | Path,
    vpn_id: str,
    *,
    env: MutableMapping[str, str] | None = None,
) -> None:
    """Delete both credential lines for one VPN."""
    names = env_var_names(vpn_id)
    _rewrite(path, dict.fromkeys(names, None))
    target = os.environ if env is None else env
    for name in names:
        target.pop(name, None)


def _present(path: str | Path, names: tuple[str, ...]) -> set[str]:
    """The names that already have an assignment line in the file."""
    path = Path(path)
    lines = path.read_text().splitlines() if path.exists() else []
    return {name for name in names if any(_assignment(name).match(line) for line in lines)}


def _assignment(name: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=")


def _quote(value: str) -> str:
    if not NEEDS_QUOTING.search(value):
        return value
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _apply(text: str, values: Mapping[str, str | None]) -> str:
    """Set or delete assignments, keeping every other line and the order."""
    patterns = {name: _assignment(name) for name in values}
    lines: list[str] = []
    written: set[str] = set()
    for line in text.splitlines():
        name = next((name for name, pattern in patterns.items() if pattern.match(line)), None)
        if name is None:
            lines.append(line)
            continue
        value = values[name]
        if value is not None and name not in written:
            lines.append(f"{name}={_quote(value)}")
        written.add(name)  # later duplicates of the same key are dropped
    for name, value in values.items():
        if value is not None and name not in written:
            lines.append(f"{name}={_quote(value)}")
    return "".join(f"{line}\n" for line in lines)


def _rewrite(path: str | Path, values: Mapping[str, str | None]) -> None:
    """Replace .env with the updated text: temp file in the same directory,
    mode 0600, then os.replace, so a reader never sees a half-written file."""
    path = Path(path)
    text = path.read_text() if path.exists() else ""
    updated = _apply(text, values)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(updated)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
