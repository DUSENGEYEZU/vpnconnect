---
noteId: "84be8730b29b11f1bfa00599ff0730c1"
tags: []

---

# vpnconnect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local Flask dashboard and REST API that connects several Cisco AnyConnect VPNs at the same time through `openconnect`, with split routing enforced so no tunnel ever takes the default route.

**Architecture:** One `openconnect` process per VPN, started and stopped through a single root-owned helper script allowed by one `sudoers.d` line. A wrapper around `vpnc-script` injects the VPN's configured routes when a server pushes a full tunnel and records the tunnel interface in a state file. A `TunnelManager` in the Flask app runs one worker thread per action and reconciles its in-memory state with the pid and iface files on every read.

**Tech Stack:** Python 3.13, uv, Flask 3, flasgger (Swagger UI at `/docs/`), python-dotenv, PyYAML, pytest, ruff, bash 3.2 (macOS `/bin/bash`), openconnect 9.21 from Homebrew.

**Spec:** `docs/superpowers/specs/2026-09-17-vpnconnect-design.md`

## Global Constraints

- Python `>=3.13`; dependencies managed with `uv` (`uv add`, `uv sync`, `uv run`).
- Runtime dependencies: `flask>=3.1.3`, `flasgger>=0.9.7.1`, `python-dotenv>=1.2.3`, `pyyaml>=6.0.2`. Dev: `pytest>=9.1.1`, `ruff>=0.16.7`. Nothing else.
- Shell scripts must run on macOS `/bin/bash` 3.2: no arrays under `set -u`, no `mapfile`, no `${var,,}`.
- Passwords travel only on stdin, never as a command-line argument, never in a log line, never in an API response.
- The server binds `127.0.0.1` only. No authentication on the API.
- VPN ids match `^[a-z0-9][a-z0-9_-]*$`. Env var names: `VPN_<ID upper, '-'→'_'>_USERNAME` / `_PASSWORD`.
- Files the helper and wrapper create live in the state dir only: `<id>.pid`, `<id>.log`, `<id>.iface`.
- `vpns.yaml` is git-ignored (it holds real VPN hostnames); `vpns.example.yaml` is committed.
- Every commit passes `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`.
- Never `git push`; Longin pushes himself. Never run `scripts/setup-privileges.sh`; Longin runs it.
- Working directory for every command: `/Users/longin/Documents/dev_projects/Longin-Project/VPN Project/vpnconnect` (note the space; quote the path).

## File structure

| File | Responsibility |
|---|---|
| `pyproject.toml`, `.python-version`, `.flaskenv`, `.gitignore` | Project metadata, tooling, Flask CLI settings |
| `vpns.example.yaml`, `.env.example` | Templates the user copies to `vpns.yaml` and `.env` |
| `app/__init__.py` | `create_app(config_class, manager=None)`; builds the real `TunnelManager` when none is given |
| `app/config.py` | `Config` class read from environment; `env_int` |
| `app/docs.py` | flasgger config and schemas (`VpnStatus`, `ActionAccepted`, `BulkResult`, `LogTail`, `Error`) |
| `app/api/__init__.py`, `health.py`, `vpns.py` | Blueprint at `/api/v1`; health; VPN endpoints |
| `app/services/registry.py` | `VpnDef`, `Registry`, `load_registry`, `save_servercert`, `RegistryError` |
| `app/services/status.py` | Pure readers: pid/iface/log files, failure parsing, pin parsing, route counting |
| `app/services/runner.py` | `HelperRunner` protocol, `SudoHelperRunner`, `RunResult`, `HelperUnavailable` |
| `app/services/tunnel.py` | `TunnelManager` state machine and workers; `UnknownVpn`, `MissingCredentials`, `InvalidTransition` |
| `app/templates/index.html` | Dashboard page |
| `scripts/vpnc-split.sh` | Split-routing wrapper around vpnc-script; writes `<id>.iface` |
| `scripts/vpnconnect-helper` | Privileged `probe` / `connect` / `disconnect` |
| `scripts/setup-privileges.sh` | One-time root install of the two scripts and the sudoers line |
| `tests/fakes.py` | `FakeRunner`, `ImmediateThread`, `FakeClock`, shared YAML and env |
| `tests/conftest.py` | `state_dir`, `vpns_file`, `runner`, `clock`, `manager`, `app`, `client` fixtures |
| `tests/test_*.py` | One test module per source module plus `test_scripts.py`, `test_docs.py`, `test_dashboard.py` |
| `README.md` | Install, privilege setup, configuration, run, API, troubleshooting |

---

### Task 1: Project scaffold and VPN registry

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `.flaskenv`, `app/__init__.py` (empty for now), `app/services/__init__.py`, `tests/__init__.py`
- Create: `app/services/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Produces: `VpnDef` (frozen dataclass: `id, name, server, authgroup, protocol="anyconnect", routes: tuple[IPv4Network,...], servercert: str|None, username: str|None, password: str|None`; properties `env_prefix`, `missing_credentials -> list[str]`, `has_credentials -> bool`; methods `routes_arg() -> str`, `public() -> dict`), `Registry` (`path: Path`, `vpns: list[VpnDef]`, `get(id) -> VpnDef|None`, `ids() -> list[str]`), `load_registry(path, env) -> Registry`, `save_servercert(path, vpn_id, value) -> None`, `RegistryError(ValueError)`, constant `TRUSTED_CA = "trusted-ca"`.

- [ ] **Step 1: Write project metadata**

`pyproject.toml`:

```toml
[project]
name = "vpnconnect"
version = "0.1.0"
description = "Run several Cisco AnyConnect VPNs at once from one local dashboard"
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
    "flasgger>=0.9.7.1",
    "flask>=3.1.3",
    "python-dotenv>=1.2.3",
    "pyyaml>=6.0.2",
]

[dependency-groups]
dev = [
    "pytest>=9.1.1",
    "ruff>=0.16.7",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
```

`.python-version`:

```
3.13
```

`.gitignore`:

```
.venv/
__pycache__/
*.pyc
.env
.pytest_cache/
.ruff_cache/
*.egg-info/
dist/
build/
.DS_Store

# Real VPN definitions (hostnames, groups, pins). Copy vpns.example.yaml instead.
vpns.yaml

# Per-tunnel pid, log and iface files written at runtime
state/
```

`.flaskenv`:

```
FLASK_APP=app
FLASK_RUN_HOST=127.0.0.1
FLASK_RUN_PORT=5000
```

Create the empty packages:

```bash
mkdir -p app/services app/api app/templates scripts state tests
touch app/__init__.py app/services/__init__.py tests/__init__.py
echo "README.md placeholder" > README.md
```

- [ ] **Step 2: Install dependencies**

Run: `uv sync`
Expected: `.venv/` and `uv.lock` created; `uv run python -c "import flask, flasgger, yaml, dotenv"` prints nothing.

- [ ] **Step 3: Write the failing registry tests**

`tests/test_registry.py`:

```python
import textwrap

import pytest

from app.services.registry import (
    TRUSTED_CA,
    RegistryError,
    load_registry,
    save_servercert,
)

VALID_YAML = textwrap.dedent("""
    vpns:
      - id: mininfra
        name: MININFRA office
        server: vpn.mininfra.example
        authgroup: Staff
        routes:
          - 10.10.0.0/16
          - 10.20.5.0/24
      - id: rica-hq
        server: vpn.rica.example:8443
        authgroup: Employees
        servercert: pin-sha256:abc123=
""")


@pytest.fixture
def vpns_file(tmp_path):
    path = tmp_path / "vpns.yaml"
    path.write_text(VALID_YAML)
    return path


def test_load_registry_parses_all_fields(vpns_file):
    registry = load_registry(vpns_file, env={})

    assert registry.ids() == ["mininfra", "rica-hq"]
    mininfra = registry.get("mininfra")
    assert mininfra.name == "MININFRA office"
    assert mininfra.server == "vpn.mininfra.example"
    assert mininfra.authgroup == "Staff"
    assert mininfra.protocol == "anyconnect"
    assert [str(r) for r in mininfra.routes] == ["10.10.0.0/16", "10.20.5.0/24"]
    assert mininfra.servercert is None


def test_name_defaults_to_id_and_servercert_is_kept(vpns_file):
    rica = load_registry(vpns_file, env={}).get("rica-hq")

    assert rica.name == "rica-hq"
    assert rica.servercert == "pin-sha256:abc123="
    assert rica.routes == ()
    assert load_registry(vpns_file, env={}).get("ghost") is None


def test_credentials_come_from_env_with_dash_mapped_to_underscore(vpns_file):
    env = {"VPN_RICA_HQ_USERNAME": "longin", "VPN_RICA_HQ_PASSWORD": "s3cret"}
    registry = load_registry(vpns_file, env=env)

    rica = registry.get("rica-hq")
    assert rica.env_prefix == "VPN_RICA_HQ"
    assert rica.username == "longin"
    assert rica.password == "s3cret"
    assert rica.has_credentials is True
    assert rica.missing_credentials == []

    mininfra = registry.get("mininfra")
    assert mininfra.has_credentials is False
    assert mininfra.missing_credentials == ["VPN_MININFRA_USERNAME", "VPN_MININFRA_PASSWORD"]


def test_blank_env_value_counts_as_missing(vpns_file):
    env = {"VPN_MININFRA_USERNAME": "longin", "VPN_MININFRA_PASSWORD": ""}

    assert load_registry(vpns_file, env=env).get("mininfra").missing_credentials == [
        "VPN_MININFRA_PASSWORD"
    ]


def test_routes_arg_builds_addr_mask_len_triples(vpns_file):
    registry = load_registry(vpns_file, env={})

    assert (
        registry.get("mininfra").routes_arg()
        == "10.10.0.0:255.255.0.0:16,10.20.5.0:255.255.255.0:24"
    )
    assert registry.get("rica-hq").routes_arg() == "-"


def test_public_never_exposes_credentials(vpns_file):
    env = {"VPN_MININFRA_USERNAME": "u", "VPN_MININFRA_PASSWORD": "p"}
    public = load_registry(vpns_file, env=env).get("mininfra").public()

    assert "password" not in public
    assert "username" not in public
    assert public["has_credentials"] is True
    assert public["missing_credentials"] == []
    assert public["routes"] == ["10.10.0.0/16", "10.20.5.0/24"]
    assert public["servercert"] is None


@pytest.mark.parametrize(
    ("yaml_text", "fragment"),
    [
        ("vpns:\n  - id: Bad Id\n    server: s\n    authgroup: g\n", "id must match"),
        ("vpns:\n  - id: ok\n    authgroup: g\n", "'server' is required"),
        ("vpns:\n  - id: ok\n    server: s\n", "'authgroup' is required"),
        (
            "vpns:\n  - id: ok\n    server: s\n    authgroup: g\n    routes: [nonsense]\n",
            "not an IPv4 network",
        ),
        (
            "vpns:\n  - id: dup\n    server: s\n    authgroup: g\n"
            "  - id: dup\n    server: s\n    authgroup: g\n",
            "duplicate id",
        ),
        ("vpns: notalist\n", "'vpns' must be a list"),
    ],
)
def test_invalid_files_raise_registry_error_naming_the_problem(tmp_path, yaml_text, fragment):
    path = tmp_path / "vpns.yaml"
    path.write_text(yaml_text)

    with pytest.raises(RegistryError, match=fragment):
        load_registry(path, env={})


def test_missing_file_raises(tmp_path):
    with pytest.raises(RegistryError, match="does not exist"):
        load_registry(tmp_path / "nope.yaml", env={})


def test_save_servercert_updates_only_that_vpn(vpns_file):
    save_servercert(vpns_file, "mininfra", "pin-sha256:NEW=")

    registry = load_registry(vpns_file, env={})
    assert registry.get("mininfra").servercert == "pin-sha256:NEW="
    assert registry.get("rica-hq").servercert == "pin-sha256:abc123="
    assert [str(r) for r in registry.get("mininfra").routes] == ["10.10.0.0/16", "10.20.5.0/24"]


def test_save_servercert_unknown_id_raises(vpns_file):
    with pytest.raises(RegistryError, match="not found"):
        save_servercert(vpns_file, "ghost", TRUSTED_CA)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_registry.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'app.services.registry'`.

- [ ] **Step 5: Implement the registry**

`app/services/registry.py`:

```python
"""VPN definitions from vpns.yaml plus credentials from the environment.

vpns.yaml holds no secrets and may be edited by hand. Credentials are read
from VPN_<ID>_USERNAME / VPN_<ID>_PASSWORD, where <ID> is the id upper-cased
with '-' replaced by '_'.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# Stored as servercert when the server certificate validates against the
# system trust store, so no --servercert pin is needed.
TRUSTED_CA = "trusted-ca"


class RegistryError(ValueError):
    """vpns.yaml is missing, malformed, or contains an invalid entry."""


@dataclass(frozen=True)
class VpnDef:
    id: str
    name: str
    server: str
    authgroup: str
    protocol: str = "anyconnect"
    routes: tuple[ipaddress.IPv4Network, ...] = ()
    servercert: str | None = None
    username: str | None = None
    password: str | None = None

    @property
    def env_prefix(self) -> str:
        return env_prefix_for(self.id)

    @property
    def missing_credentials(self) -> list[str]:
        missing = []
        if not self.username:
            missing.append(f"{self.env_prefix}_USERNAME")
        if not self.password:
            missing.append(f"{self.env_prefix}_PASSWORD")
        return missing

    @property
    def has_credentials(self) -> bool:
        return not self.missing_credentials

    def routes_arg(self) -> str:
        """Routes as 'addr:mask:len,...' for the helper, or '-' when none."""
        if not self.routes:
            return "-"
        return ",".join(
            f"{net.network_address}:{net.netmask}:{net.prefixlen}" for net in self.routes
        )

    def public(self) -> dict[str, Any]:
        """Fields that may be returned from the API. Never credentials."""
        return {
            "id": self.id,
            "name": self.name,
            "server": self.server,
            "authgroup": self.authgroup,
            "protocol": self.protocol,
            "routes": [str(net) for net in self.routes],
            "servercert": self.servercert,
            "has_credentials": self.has_credentials,
            "missing_credentials": self.missing_credentials,
        }


@dataclass
class Registry:
    path: Path
    vpns: list[VpnDef] = field(default_factory=list)

    def get(self, vpn_id: str) -> VpnDef | None:
        return next((vpn for vpn in self.vpns if vpn.id == vpn_id), None)

    def ids(self) -> list[str]:
        return [vpn.id for vpn in self.vpns]


def env_prefix_for(vpn_id: str) -> str:
    return "VPN_" + vpn_id.upper().replace("-", "_")


def _require(entry: Mapping[str, Any], key: str, label: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"vpns[{label}]: '{key}' is required and must be a non-empty string")
    return value.strip()


def parse_vpn(entry: Mapping[str, Any], index: int, env: Mapping[str, str]) -> VpnDef:
    label = str(entry.get("id") or f"#{index}")
    vpn_id = _require(entry, "id", label)
    if not ID_PATTERN.match(vpn_id):
        raise RegistryError(f"vpns[{vpn_id}]: id must match {ID_PATTERN.pattern}")

    routes = []
    for raw in entry.get("routes") or []:
        try:
            routes.append(ipaddress.IPv4Network(str(raw), strict=False))
        except ValueError as exc:
            raise RegistryError(f"vpns[{vpn_id}]: route {raw!r} is not an IPv4 network") from exc

    servercert = entry.get("servercert")
    prefix = env_prefix_for(vpn_id)
    return VpnDef(
        id=vpn_id,
        name=str(entry.get("name") or vpn_id),
        server=_require(entry, "server", vpn_id),
        authgroup=_require(entry, "authgroup", vpn_id),
        protocol=str(entry.get("protocol") or "anyconnect"),
        routes=tuple(routes),
        servercert=str(servercert) if servercert else None,
        username=env.get(f"{prefix}_USERNAME") or None,
        password=env.get(f"{prefix}_PASSWORD") or None,
    )


def load_registry(path: str | Path, env: Mapping[str, str]) -> Registry:
    path = Path(path)
    if not path.exists():
        raise RegistryError(f"{path} does not exist")
    data = yaml.safe_load(path.read_text()) or {}
    entries = data.get("vpns") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise RegistryError(f"{path}: top-level 'vpns' must be a list")

    vpns = [parse_vpn(entry or {}, index, env) for index, entry in enumerate(entries)]
    seen: set[str] = set()
    for vpn in vpns:
        if vpn.id in seen:
            raise RegistryError(f"vpns[{vpn.id}]: duplicate id")
        seen.add(vpn.id)
    return Registry(path=path, vpns=vpns)


def save_servercert(path: str | Path, vpn_id: str, value: str) -> None:
    """Write servercert for one VPN back into vpns.yaml, keeping every other key.

    PyYAML does not keep comments, so a hand-written comment in vpns.yaml is
    lost the first time the app stores a pin. The README says so.
    """
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}
    for entry in data.get("vpns") or []:
        if isinstance(entry, dict) and entry.get("id") == vpn_id:
            entry["servercert"] = value
            break
    else:
        raise RegistryError(f"vpns[{vpn_id}]: not found")
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_registry.py -q`
Expected: all pass.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add pyproject.toml uv.lock .python-version .gitignore .flaskenv README.md app tests
git commit -m "feat: project scaffold and VPN registry

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Status readers

**Files:**
- Create: `app/services/status.py`
- Test: `tests/test_status.py`

**Interfaces:**
- Produces: `pid_path(state_dir, id)`, `log_path(state_dir, id)`, `iface_path(state_dir, id)` (all `-> Path`); `read_pid(state_dir, id) -> int|None`; `pid_alive(pid) -> bool`; `read_iface(state_dir, id) -> tuple[str,str]|None`; `read_log_tail(state_dir, id, lines=50) -> list[str]`; `strip_timestamp(line) -> str`; `parse_failure(log_text) -> str|None`; `pin_from_probe(output) -> str|None`; `server_reachable(output) -> bool`; `count_routes(interface, netstat_output=None) -> int`.

- [ ] **Step 1: Write the failing tests**

`tests/test_status.py`:

```python
import os
import subprocess

import pytest

from app.services import status

NETSTAT = """\
Routing tables

Internet:
Destination        Gateway            Flags               Netif Expire
default            10.8.120.1         UGScg                 en0
10.10.34.10/32     10.10.47.29        UGSc                utun6
10.10.34.11/32     10.10.47.29        UGSc                utun6
10.10.47.24/29     link#25            UCS                 utun6
192.168.1.1        a8:6b:ad:1:2:3     UHLWIir               en0   1189
224.0.0/4          link#25            UmCSI               utun6
"""


def test_paths_are_derived_from_state_dir_and_id(tmp_path):
    assert status.pid_path(tmp_path, "x") == tmp_path / "x.pid"
    assert status.log_path(tmp_path, "x") == tmp_path / "x.log"
    assert status.iface_path(tmp_path, "x") == tmp_path / "x.iface"


def test_read_pid_returns_none_when_missing_or_garbage(tmp_path):
    assert status.read_pid(tmp_path, "x") is None
    (tmp_path / "x.pid").write_text("abc\n")
    assert status.read_pid(tmp_path, "x") is None


def test_read_pid_parses_number(tmp_path):
    (tmp_path / "x.pid").write_text(" 4242 \n")
    assert status.read_pid(tmp_path, "x") == 4242


def test_pid_alive_for_own_process_and_finished_child():
    assert status.pid_alive(os.getpid()) is True
    child = subprocess.Popen(["true"])
    child.wait()
    assert status.pid_alive(child.pid) is False


def test_pid_alive_treats_permission_error_as_alive(monkeypatch):
    def deny(pid, sig):
        raise PermissionError

    monkeypatch.setattr(status.os, "kill", deny)
    assert status.pid_alive(1) is True


def test_read_iface(tmp_path):
    assert status.read_iface(tmp_path, "x") is None
    (tmp_path / "x.iface").write_text("utun7 10.10.47.29\n")
    assert status.read_iface(tmp_path, "x") == ("utun7", "10.10.47.29")
    (tmp_path / "x.iface").write_text("utun7\n")
    assert status.read_iface(tmp_path, "x") is None


def test_read_log_tail_returns_last_non_empty_lines(tmp_path):
    (tmp_path / "x.log").write_text("a\n\nb\nc\n")
    assert status.read_log_tail(tmp_path, "x", lines=2) == ["b", "c"]
    assert status.read_log_tail(tmp_path, "x") == ["a", "b", "c"]
    assert status.read_log_tail(tmp_path, "missing") == []


def test_strip_timestamp():
    assert status.strip_timestamp("[2026-09-17 14:00:01] Login failed.") == "Login failed."
    assert status.strip_timestamp("no stamp") == "no stamp"


@pytest.mark.parametrize(
    ("log", "expected"),
    [
        (
            "[2026-09-17 14:00:01] Connected to HTTPS on x\n[2026-09-17 14:00:02] Login failed.\n",
            "Login failed.",
        ),
        (
            'Certificate from VPN server "x" failed verification.\nReason: signer not found\n',
            'Certificate from VPN server "x" failed verification.',
        ),
        (
            "vpnconnect: server pushed a full tunnel and no routes are configured\n"
            "Script '/x' returned error 1\n",
            "vpnconnect: server pushed a full tunnel and no routes are configured",
        ),
        ("Script '/x' returned error 1\nsomething else\n", "Script '/x' returned error 1"),
        ("Failed to connect to host vpn.x\n", "Failed to connect to host vpn.x"),
        ("sudo: a password is required\n", "sudo: a password is required"),
        ("Continuing in background; pid 77\n", "Continuing in background; pid 77"),
        ("", None),
        ("\n\n", None),
    ],
)
def test_parse_failure_prefers_specific_messages_then_falls_back_to_last_line(log, expected):
    assert status.parse_failure(log) == expected


def test_pin_from_probe():
    out = (
        "To trust this server in future, perhaps add this to your command line:\n"
        "    --servercert pin-sha256:0Yl6a3cSB6AQ8r5k7fT9m6yLxWvqNzR2pCd3eF4gH5I=\n"
    )
    assert status.pin_from_probe(out) == "pin-sha256:0Yl6a3cSB6AQ8r5k7fT9m6yLxWvqNzR2pCd3eF4gH5I="
    assert status.pin_from_probe("Login failed.") is None


def test_server_reachable():
    reachable = "Connected to HTTPS on vpn.x with ciphersuite (TLS1.3)\nLogin failed.\n"
    assert status.server_reachable(reachable) is True
    assert status.server_reachable("Got HTTP response: HTTP/1.1 200 OK\n") is True
    assert status.server_reachable("Failed to connect to host vpn.x\n") is False
    assert status.server_reachable("") is False


def test_count_routes_counts_lines_on_interface():
    assert status.count_routes("utun6", NETSTAT) == 4
    assert status.count_routes("en0", NETSTAT) == 2
    assert status.count_routes("utun9", NETSTAT) == 0


def test_count_routes_runs_netstat_when_no_output_given(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd == ["netstat", "-rn", "-f", "inet"]
        return subprocess.CompletedProcess(cmd, 0, stdout=NETSTAT, stderr="")

    monkeypatch.setattr(status.subprocess, "run", fake_run)
    assert status.count_routes("utun6") == 4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_status.py -q`
Expected: `ModuleNotFoundError: No module named 'app.services.status'`.

- [ ] **Step 3: Implement status readers**

`app/services/status.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_status.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add app/services/status.py tests/test_status.py
git commit -m "feat: status readers for pid, iface, log and routes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Sudo helper runner

**Files:**
- Create: `app/services/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `VpnDef` (`id, server, authgroup, protocol, username, password, servercert, routes_arg()`), `TRUSTED_CA` from Task 1.
- Produces: `RunResult(returncode: int, output: str)` dataclass; `HelperUnavailable(RuntimeError)`; `HelperRunner` Protocol with `probe(vpn) -> RunResult`, `connect(vpn) -> RunResult`, `disconnect(vpn_id) -> RunResult`; `SudoHelperRunner(helper_path, connect_timeout=30, disconnect_grace=5)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_runner.py`:

```python
import dataclasses
import ipaddress
import subprocess

import pytest

from app.services.registry import VpnDef
from app.services.runner import HelperUnavailable, RunResult, SudoHelperRunner

VPN = VpnDef(
    id="mininfra",
    name="MININFRA",
    server="vpn.example",
    authgroup="Staff",
    routes=(ipaddress.IPv4Network("10.10.0.0/16"),),
    servercert="pin-sha256:abc=",
    username="longin",
    password="s3cret",
)
HELPER = "/usr/local/libexec/vpnconnect/vpnconnect-helper"


class FakeRun:
    def __init__(self):
        self.calls = []
        self.returncode = 0
        self.stdout = ""
        self.stderr = ""
        self.raise_exc = None

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.raise_exc is not None:
            raise self.raise_exc
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


@pytest.fixture
def fake_run(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr("app.services.runner.subprocess.run", fake)
    return fake


def test_connect_builds_helper_command_and_passes_password_on_stdin(fake_run):
    fake_run.stdout = "out"
    runner = SudoHelperRunner(HELPER, connect_timeout=30, disconnect_grace=5)

    result = runner.connect(VPN)

    cmd, kwargs = fake_run.calls[0]
    assert cmd == [
        "sudo",
        "-n",
        HELPER,
        "connect",
        "mininfra",
        "vpn.example",
        "Staff",
        "anyconnect",
        "longin",
        "pin-sha256:abc=",
        "10.10.0.0:255.255.0.0:16",
    ]
    assert kwargs["input"] == "s3cret\n"
    assert "s3cret" not in " ".join(cmd)
    assert kwargs["timeout"] == 45
    assert kwargs["text"] is True
    assert result == RunResult(0, "out")


def test_connect_uses_trusted_ca_and_dash_when_unset(fake_run):
    vpn = dataclasses.replace(VPN, servercert=None, routes=())

    SudoHelperRunner(HELPER).connect(vpn)

    assert fake_run.calls[0][0][-2:] == ["trusted-ca", "-"]


def test_probe_and_disconnect_commands(fake_run):
    runner = SudoHelperRunner(HELPER, disconnect_grace=7)

    runner.probe(VPN)
    runner.disconnect("mininfra")

    assert fake_run.calls[0][0] == ["sudo", "-n", HELPER, "probe", "vpn.example", "Staff", "anyconnect"]
    assert fake_run.calls[0][1]["input"] is None
    assert fake_run.calls[1][0] == ["sudo", "-n", HELPER, "disconnect", "mininfra", "7"]


def test_output_combines_stdout_and_stderr(fake_run):
    fake_run.stdout = "a\n"
    fake_run.stderr = "b\n"

    assert SudoHelperRunner(HELPER).probe(VPN).output == "a\nb\n"


def test_sudo_refusal_raises_helper_unavailable(fake_run):
    fake_run.returncode = 1
    fake_run.stderr = "sudo: a password is required\n"

    with pytest.raises(HelperUnavailable, match="password is required"):
        SudoHelperRunner(HELPER).disconnect("mininfra")


def test_missing_helper_raises_helper_unavailable(fake_run):
    fake_run.returncode = 1
    fake_run.stderr = f"sudo: {HELPER}: command not found\n"

    with pytest.raises(HelperUnavailable, match="command not found"):
        SudoHelperRunner(HELPER).connect(VPN)


def test_missing_sudo_binary_raises_helper_unavailable(fake_run):
    fake_run.raise_exc = FileNotFoundError("sudo")

    with pytest.raises(HelperUnavailable):
        SudoHelperRunner(HELPER).connect(VPN)


def test_nonzero_exit_from_openconnect_is_returned_not_raised(fake_run):
    fake_run.returncode = 2
    fake_run.stderr = "vpnconnect-helper: something\n"

    result = SudoHelperRunner(HELPER).connect(VPN)

    assert result.returncode == 2
    assert result.output == "vpnconnect-helper: something\n"


def test_timeout_is_reported_as_result(fake_run):
    fake_run.raise_exc = subprocess.TimeoutExpired(cmd="x", timeout=45)

    result = SudoHelperRunner(HELPER).connect(VPN)

    assert result.returncode == 124
    assert "timed out" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_runner.py -q`
Expected: `ModuleNotFoundError: No module named 'app.services.runner'`.

- [ ] **Step 3: Implement the runner**

`app/services/runner.py`:

```python
"""The single place that spawns processes: `sudo -n <helper> ...`.

Everything else in the app talks to a HelperRunner, so tests swap in a fake
and never touch sudo or openconnect.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol

from app.services.registry import TRUSTED_CA, VpnDef


@dataclass(frozen=True)
class RunResult:
    returncode: int
    output: str


class HelperUnavailable(RuntimeError):
    """sudo refused (no NOPASSWD rule) or the helper is not installed."""


class HelperRunner(Protocol):
    def probe(self, vpn: VpnDef) -> RunResult: ...

    def connect(self, vpn: VpnDef) -> RunResult: ...

    def disconnect(self, vpn_id: str) -> RunResult: ...


class SudoHelperRunner:
    def __init__(self, helper_path: str, connect_timeout: int = 30, disconnect_grace: int = 5):
        self.helper_path = helper_path
        self.connect_timeout = connect_timeout
        self.disconnect_grace = disconnect_grace

    def _run(self, args: list[str], stdin: str | None, timeout: int) -> RunResult:
        cmd = ["sudo", "-n", self.helper_path, *args]
        try:
            proc = subprocess.run(
                cmd, input=stdin, capture_output=True, text=True, timeout=timeout, check=False
            )
        except FileNotFoundError as exc:
            raise HelperUnavailable(str(exc)) from exc
        except subprocess.TimeoutExpired:
            return RunResult(124, f"helper timed out after {timeout}s")
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0 and output.lstrip().startswith("sudo:"):
            raise HelperUnavailable(output.strip())
        return RunResult(proc.returncode, output)

    def probe(self, vpn: VpnDef) -> RunResult:
        return self._run(["probe", vpn.server, vpn.authgroup, vpn.protocol], stdin=None, timeout=60)

    def connect(self, vpn: VpnDef) -> RunResult:
        if not vpn.username or not vpn.password:
            raise ValueError(f"{vpn.id}: credentials missing")
        args = [
            "connect",
            vpn.id,
            vpn.server,
            vpn.authgroup,
            vpn.protocol,
            vpn.username,
            vpn.servercert or TRUSTED_CA,
            vpn.routes_arg(),
        ]
        # openconnect --background returns once the tunnel is up or auth failed;
        # give it the same budget as the manager plus a margin.
        return self._run(args, stdin=vpn.password + "\n", timeout=self.connect_timeout + 15)

    def disconnect(self, vpn_id: str) -> RunResult:
        return self._run(
            ["disconnect", vpn_id, str(self.disconnect_grace)], stdin=None, timeout=30
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_runner.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add app/services/runner.py tests/test_runner.py
git commit -m "feat: sudo helper runner

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: TunnelManager state machine

**Files:**
- Create: `tests/fakes.py`, `app/services/tunnel.py`
- Test: `tests/test_tunnel.py`

**Interfaces:**
- Consumes: `Registry`, `VpnDef`, `save_servercert`, `TRUSTED_CA` (Task 1); `status` functions (Task 2); `HelperRunner`, `RunResult`, `HelperUnavailable` (Task 3).
- Produces: constants `DISCONNECTED, CONNECTING, CONNECTED, DISCONNECTING, ERROR`, `HELPER_HINT`, `PIN_HINT`; exceptions `TunnelError`, `UnknownVpn`, `MissingCredentials(missing: list[str])`, `InvalidTransition`; `TunnelManager(registry, runner, state_dir, *, connect_timeout=30, poll_interval=0.5, pid_alive=..., route_counter=..., thread_factory=..., clock=..., sleep=...)` with `connect(id)`, `disconnect(id)`, `connect_all() -> {"started": [...], "skipped": [{"id","reason"}]}`, `disconnect_all()` (same shape), `snapshot() -> list[dict]`, `snapshot_one(id) -> dict`, `log_tail(id, lines=50) -> list[str]`, `refresh()`.
- Test fakes in `tests/fakes.py`: `VPNS_YAML`, `ENV`, `ImmediateThread`, `FakeRunner(state_dir)` with `alive: set[int]`, `calls: list`, `probe_output: str`, `connect_mode: "ok"|"login-failed"|"cert-mismatch"|"no-iface"|"die"|"helper-missing"`, `FakeClock()` callable with `.sleep(s)`.

- [ ] **Step 1: Write the shared fakes**

`tests/fakes.py`:

```python
"""Test doubles shared by the tunnel and API tests."""

from __future__ import annotations

from pathlib import Path

from app.services.runner import HelperUnavailable, RunResult

VPNS_YAML = """\
vpns:
  - id: mininfra
    name: MININFRA
    server: vpn.mininfra.example
    authgroup: Staff
    routes: [10.10.0.0/16]
    servercert: pin-sha256:known=
  - id: rica
    name: RICA
    server: vpn.rica.example
    authgroup: Employees
"""

ENV = {
    "VPN_MININFRA_USERNAME": "longin",
    "VPN_MININFRA_PASSWORD": "pw1",
    "VPN_RICA_USERNAME": "longin",
    "VPN_RICA_PASSWORD": "pw2",
}

PROBE_WITH_PIN = (
    'Certificate from VPN server "vpn.rica.example" failed verification.\n'
    "To trust this server in future, perhaps add this to your command line:\n"
    "    --servercert pin-sha256:probed=\n"
)
CONFIGURED_LINE = "Configured as 10.9.9.9, with SSL connected and DTLS in progress"


class ImmediateThread:
    """Runs the target synchronously so tests observe the final state right away."""

    def __init__(self, target, args=(), daemon=True):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


class FakeRunner:
    """Pretends to be the sudo helper: writes the files openconnect and the wrapper would."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.alive: set[int] = set()
        self.calls: list[tuple] = []
        self.next_pid = 4000
        self.probe_output = PROBE_WITH_PIN
        self.connect_mode = "ok"
        self.log_text = CONFIGURED_LINE + "\n"

    def probe(self, vpn):
        self.calls.append(("probe", vpn.id))
        return RunResult(0, self.probe_output)

    def connect(self, vpn):
        self.calls.append(("connect", vpn.id, vpn.servercert, vpn.routes_arg()))
        if self.connect_mode == "helper-missing":
            raise HelperUnavailable("sudo: a password is required")
        log = self.state_dir / f"{vpn.id}.log"
        if self.connect_mode == "login-failed":
            log.write_text("[t] Connected to HTTPS on x\n[t] Login failed.\n")
            return RunResult(1, "")
        if self.connect_mode == "cert-mismatch":
            log.write_text(
                '[t] Certificate from VPN server "vpn.mininfra.example" failed verification.\n'
                "[t] Reason: certificate does not match pin\n"
            )
            return RunResult(1, "")
        self.next_pid += 1
        pid = self.next_pid
        (self.state_dir / f"{vpn.id}.pid").write_text(f"{pid}\n")
        if self.connect_mode == "die":
            log.write_text("[t] Script '/x/vpnc-split.sh' returned error 1\n")
            return RunResult(0, "")  # pid file exists but the process is not alive
        log.write_text(self.log_text)
        self.alive.add(pid)
        if self.connect_mode != "no-iface":
            (self.state_dir / f"{vpn.id}.iface").write_text("utun9 10.9.9.9\n")
        return RunResult(0, "")

    def disconnect(self, vpn_id):
        self.calls.append(("disconnect", vpn_id))
        pidfile = self.state_dir / f"{vpn_id}.pid"
        if pidfile.exists():
            text = pidfile.read_text().strip()
            if text.isdigit():
                self.alive.discard(int(text))
            pidfile.unlink()
        (self.state_dir / f"{vpn_id}.iface").unlink(missing_ok=True)
        return RunResult(0, "")


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
```

- [ ] **Step 2: Write the failing tests**

`tests/test_tunnel.py`:

```python
import pytest

from app.services import tunnel
from app.services.registry import load_registry
from app.services.tunnel import (
    CONNECTED,
    DISCONNECTED,
    ERROR,
    InvalidTransition,
    MissingCredentials,
    TunnelManager,
    UnknownVpn,
)
from tests.fakes import ENV, VPNS_YAML, FakeClock, FakeRunner, ImmediateThread


@pytest.fixture
def state_dir(tmp_path):
    path = tmp_path / "state"
    path.mkdir()
    return path


@pytest.fixture
def vpns_file(tmp_path):
    path = tmp_path / "vpns.yaml"
    path.write_text(VPNS_YAML)
    return path


@pytest.fixture
def runner(state_dir):
    return FakeRunner(state_dir)


@pytest.fixture
def clock():
    return FakeClock()


def make_manager(vpns_file, runner, state_dir, clock, env=ENV):
    return TunnelManager(
        load_registry(vpns_file, env),
        runner,
        state_dir,
        connect_timeout=30,
        poll_interval=0.5,
        pid_alive=lambda pid: pid in runner.alive,
        route_counter=lambda iface: 3,
        thread_factory=ImmediateThread,
        clock=clock,
        sleep=clock.sleep,
    )


@pytest.fixture
def manager(vpns_file, runner, state_dir, clock):
    return make_manager(vpns_file, runner, state_dir, clock)


def test_initial_snapshot_is_disconnected_with_public_fields(manager):
    snap = manager.snapshot()

    assert [s["id"] for s in snap] == ["mininfra", "rica"]
    assert all(s["state"] == DISCONNECTED for s in snap)
    assert snap[0]["routes"] == ["10.10.0.0/16"]
    assert snap[0]["routes_count"] == 0
    assert snap[0]["interface"] is None
    assert "password" not in snap[0]


def test_connect_success_marks_connected_with_interface(manager, runner):
    manager.connect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == CONNECTED
    assert s["interface"] == "utun9"
    assert s["ip"] == "10.9.9.9"
    assert s["routes_count"] == 3
    assert s["since"]
    assert ("connect", "mininfra", "pin-sha256:known=", "10.10.0.0:255.255.0.0:16") in runner.calls


def test_connect_probes_and_saves_pin_when_servercert_missing(manager, runner, vpns_file):
    manager.connect("rica")

    assert runner.calls[0] == ("probe", "rica")
    assert runner.calls[1][:3] == ("connect", "rica", "pin-sha256:probed=")
    assert "pin-sha256:probed=" in vpns_file.read_text()
    assert manager.snapshot_one("rica")["servercert"] == "pin-sha256:probed="


def test_probe_without_pin_but_reachable_stores_trusted_ca(manager, runner):
    runner.probe_output = "Connected to HTTPS on vpn.rica.example with ciphersuite X\nLogin failed.\n"

    manager.connect("rica")

    assert runner.calls[1][:3] == ("connect", "rica", "trusted-ca")
    assert manager.snapshot_one("rica")["state"] == CONNECTED


def test_probe_that_cannot_reach_server_is_an_error(manager, runner):
    runner.probe_output = "Failed to connect to host vpn.rica.example\n"

    manager.connect("rica")

    s = manager.snapshot_one("rica")
    assert s["state"] == ERROR
    assert s["message"] == (
        "could not probe server certificate: Failed to connect to host vpn.rica.example"
    )
    assert not any(call[0] == "connect" for call in runner.calls)


def test_login_failure_is_reported_from_log(manager, runner):
    runner.connect_mode = "login-failed"

    manager.connect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == ERROR
    assert s["message"] == "Login failed."


def test_certificate_mismatch_tells_how_to_reprobe(manager, runner):
    runner.connect_mode = "cert-mismatch"

    manager.connect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == ERROR
    assert s["message"] == (
        'Certificate from VPN server "vpn.mininfra.example" failed verification. '
        + tunnel.PIN_HINT
    )


def test_process_dying_before_tunnel_is_reported(manager, runner, state_dir):
    runner.connect_mode = "die"

    manager.connect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == ERROR
    assert s["message"] == "Script '/x/vpnc-split.sh' returned error 1"
    assert not (state_dir / "mininfra.pid").exists()


def test_timeout_disconnects_and_reports(manager, runner):
    runner.connect_mode = "no-iface"

    manager.connect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == ERROR
    assert s["message"].startswith("timed out after 30 s: Configured as 10.9.9.9")
    assert ("disconnect", "mininfra") in runner.calls


def test_helper_unavailable_gives_setup_hint(manager, runner):
    runner.connect_mode = "helper-missing"

    manager.connect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == ERROR
    assert s["message"] == tunnel.HELPER_HINT


def test_missing_credentials_and_unknown_id(vpns_file, runner, state_dir, clock):
    m = make_manager(vpns_file, runner, state_dir, clock, env={"VPN_MININFRA_USERNAME": "longin"})

    with pytest.raises(MissingCredentials) as exc:
        m.connect("mininfra")
    assert exc.value.missing == ["VPN_MININFRA_PASSWORD"]
    assert str(exc.value) == "missing credentials: VPN_MININFRA_PASSWORD"
    with pytest.raises(UnknownVpn):
        m.connect("ghost")
    with pytest.raises(UnknownVpn):
        m.snapshot_one("ghost")
    with pytest.raises(UnknownVpn):
        m.disconnect("ghost")


def test_connect_twice_is_invalid(manager):
    manager.connect("mininfra")

    with pytest.raises(InvalidTransition, match="mininfra is already connected"):
        manager.connect("mininfra")


def test_disconnect_flow(manager, runner, state_dir):
    with pytest.raises(InvalidTransition, match="mininfra is not connected"):
        manager.disconnect("mininfra")

    manager.connect("mininfra")
    manager.disconnect("mininfra")

    assert manager.snapshot_one("mininfra")["state"] == DISCONNECTED
    assert ("disconnect", "mininfra") in runner.calls
    assert not (state_dir / "mininfra.iface").exists()


def test_disconnect_clears_error_state(manager, runner):
    runner.connect_mode = "login-failed"
    manager.connect("mininfra")

    manager.disconnect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == DISCONNECTED
    assert s["message"] is None


def test_refresh_detects_dropped_tunnel(manager, runner, state_dir):
    manager.connect("mininfra")
    runner.alive.clear()  # openconnect died on its own
    (state_dir / "mininfra.log").write_text(
        "[t] Configured as 10.9.9.9\n[t] SSL read error; reconnecting.\n"
        "[t] Failed to reconnect to host\n"
    )

    s = manager.snapshot_one("mininfra")

    assert s["state"] == ERROR
    assert s["message"] == "tunnel dropped: Failed to reconnect to host"
    assert not (state_dir / "mininfra.pid").exists()


def test_refresh_adopts_tunnel_running_from_previous_app_instance(
    vpns_file, runner, state_dir, clock
):
    (state_dir / "mininfra.pid").write_text("5555\n")
    (state_dir / "mininfra.iface").write_text("utun4 10.1.1.1\n")
    runner.alive.add(5555)

    s = make_manager(vpns_file, runner, state_dir, clock).snapshot_one("mininfra")

    assert s["state"] == CONNECTED
    assert s["interface"] == "utun4"
    assert s["ip"] == "10.1.1.1"
    assert s["message"] == "adopted running tunnel"


def test_refresh_cleans_stale_pid_file_without_error(vpns_file, runner, state_dir, clock):
    (state_dir / "mininfra.pid").write_text("5555\n")  # not alive, never connected here

    s = make_manager(vpns_file, runner, state_dir, clock).snapshot_one("mininfra")

    assert s["state"] == DISCONNECTED
    assert not (state_dir / "mininfra.pid").exists()


def test_connect_all_and_disconnect_all(vpns_file, runner, state_dir, clock):
    m = make_manager(vpns_file, runner, state_dir, clock, env={**ENV, "VPN_RICA_PASSWORD": ""})

    result = m.connect_all()
    assert result["started"] == ["mininfra"]
    assert result["skipped"] == [
        {"id": "rica", "reason": "missing credentials: VPN_RICA_PASSWORD"}
    ]

    again = m.connect_all()
    assert again["started"] == []
    assert again["skipped"][0] == {"id": "mininfra", "reason": "mininfra is already connected"}

    off = m.disconnect_all()
    assert off["started"] == ["mininfra"]
    assert off["skipped"] == [{"id": "rica", "reason": "rica is not connected"}]


def test_log_tail_strips_timestamps(manager, runner, state_dir):
    manager.connect("mininfra")
    (state_dir / "mininfra.log").write_text("[2026-09-17 14:00:00] first\n[2026-09-17 14:00:01] second\n")

    assert manager.log_tail("mininfra", lines=1) == ["second"]
    assert manager.log_tail("mininfra") == ["first", "second"]
    with pytest.raises(UnknownVpn):
        manager.log_tail("ghost")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tunnel.py -q`
Expected: `ModuleNotFoundError: No module named 'app.services.tunnel'`.

- [ ] **Step 4: Implement the TunnelManager**

`app/services/tunnel.py`:

```python
"""Tunnel state machine.

One worker thread per connect or disconnect. In-memory state is reconciled
with the pid and iface files on every read, so the dashboard survives app
restarts and notices tunnels that drop on their own.

    disconnected --connect()--> connecting --iface file + live pid--> connected
    connecting   --nonzero exit / dead pid / timeout--> error(message)
    connected    --disconnect()--> disconnecting --helper done--> disconnected
    connected    --pid gone (seen on refresh)--> error("tunnel dropped: ...")
    error        --connect() / disconnect()--> as above
"""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services import status as st
from app.services.registry import TRUSTED_CA, Registry, VpnDef, save_servercert
from app.services.runner import HelperRunner, HelperUnavailable

DISCONNECTED = "disconnected"
CONNECTING = "connecting"
CONNECTED = "connected"
DISCONNECTING = "disconnecting"
ERROR = "error"

HELPER_HINT = "privilege helper not available: run sudo scripts/setup-privileges.sh"
PIN_HINT = "(delete servercert for this VPN in vpns.yaml to trust the new certificate)"


class TunnelError(Exception):
    """Base class for errors the API turns into HTTP status codes."""


class UnknownVpn(TunnelError):
    pass


class MissingCredentials(TunnelError):
    def __init__(self, missing: list[str]) -> None:
        super().__init__("missing credentials: " + ", ".join(missing))
        self.missing = missing


class InvalidTransition(TunnelError):
    pass


@dataclass(frozen=True)
class TunnelState:
    state: str = DISCONNECTED
    interface: str | None = None
    ip: str | None = None
    message: str | None = None
    since: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TunnelManager:
    def __init__(
        self,
        registry: Registry,
        runner: HelperRunner,
        state_dir: Path,
        *,
        connect_timeout: float = 30,
        poll_interval: float = 0.5,
        pid_alive: Callable[[int], bool] = st.pid_alive,
        route_counter: Callable[[str], int] = st.count_routes,
        thread_factory: Callable[..., Any] = threading.Thread,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.registry = registry
        self.runner = runner
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.connect_timeout = connect_timeout
        self.poll_interval = poll_interval
        self._pid_alive = pid_alive
        self._route_counter = route_counter
        self._thread_factory = thread_factory
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.RLock()
        self._states: dict[str, TunnelState] = {vpn.id: TunnelState() for vpn in registry.vpns}

    # ----- lookups -------------------------------------------------------

    def _vpn(self, vpn_id: str) -> VpnDef:
        vpn = self.registry.get(vpn_id)
        if vpn is None:
            raise UnknownVpn(f"unknown vpn: {vpn_id}")
        return vpn

    def state_of(self, vpn_id: str) -> TunnelState:
        with self._lock:
            return self._states[vpn_id]

    def _set(
        self,
        vpn_id: str,
        state: str,
        *,
        interface: str | None = None,
        ip: str | None = None,
        message: str | None = None,
    ) -> None:
        with self._lock:
            self._states[vpn_id] = TunnelState(state, interface, ip, message, _now())

    def _clear_files(self, vpn_id: str) -> None:
        st.pid_path(self.state_dir, vpn_id).unlink(missing_ok=True)
        st.iface_path(self.state_dir, vpn_id).unlink(missing_ok=True)

    def _spawn(self, target: Callable[..., None], *args: Any) -> None:
        self._thread_factory(target=target, args=args, daemon=True).start()

    # ----- reconciliation ------------------------------------------------

    def refresh(self) -> None:
        """Bring in-memory state in line with the pid and iface files on disk."""
        for vpn in self.registry.vpns:
            with self._lock:
                current = self._states[vpn.id]
                if current.state in (CONNECTING, DISCONNECTING):
                    continue  # a worker owns this id right now
                pid = st.read_pid(self.state_dir, vpn.id)
                alive = pid is not None and self._pid_alive(pid)
                if alive:
                    iface = st.read_iface(self.state_dir, vpn.id)
                    if current.state != CONNECTED and iface is not None:
                        self._set(
                            vpn.id,
                            CONNECTED,
                            interface=iface[0],
                            ip=iface[1],
                            message="adopted running tunnel",
                        )
                    continue
                if pid is not None:
                    self._clear_files(vpn.id)
                if current.state == CONNECTED:
                    reason = self._failure_message(vpn.id) or "process exited"
                    self._set(vpn.id, ERROR, message=f"tunnel dropped: {reason}")

    # ----- actions -------------------------------------------------------

    def connect(self, vpn_id: str) -> None:
        vpn = self._vpn(vpn_id)
        if vpn.missing_credentials:
            raise MissingCredentials(vpn.missing_credentials)
        self.refresh()
        with self._lock:
            current = self._states[vpn_id].state
            if current in (CONNECTING, CONNECTED, DISCONNECTING):
                raise InvalidTransition(f"{vpn_id} is already {current}")
            self._set(vpn_id, CONNECTING)
        self._spawn(self._connect_worker, vpn)

    def disconnect(self, vpn_id: str) -> None:
        self._vpn(vpn_id)
        self.refresh()
        with self._lock:
            current = self._states[vpn_id].state
            if current in (CONNECTING, DISCONNECTING):
                raise InvalidTransition(f"{vpn_id} is busy ({current}); try again shortly")
            has_pid = st.read_pid(self.state_dir, vpn_id) is not None
            if current == DISCONNECTED and not has_pid:
                raise InvalidTransition(f"{vpn_id} is not connected")
            self._set(vpn_id, DISCONNECTING)
        self._spawn(self._disconnect_worker, vpn_id)

    def connect_all(self) -> dict[str, list]:
        started: list[str] = []
        skipped: list[dict[str, str]] = []
        for vpn in self.registry.vpns:
            try:
                self.connect(vpn.id)
                started.append(vpn.id)
            except (MissingCredentials, InvalidTransition) as exc:
                skipped.append({"id": vpn.id, "reason": str(exc)})
        return {"started": started, "skipped": skipped}

    def disconnect_all(self) -> dict[str, list]:
        started: list[str] = []
        skipped: list[dict[str, str]] = []
        for vpn in self.registry.vpns:
            try:
                self.disconnect(vpn.id)
                started.append(vpn.id)
            except InvalidTransition as exc:
                skipped.append({"id": vpn.id, "reason": str(exc)})
        return {"started": started, "skipped": skipped}

    # ----- reads ---------------------------------------------------------

    def snapshot(self) -> list[dict[str, Any]]:
        self.refresh()
        return [self._describe(vpn) for vpn in self.registry.vpns]

    def snapshot_one(self, vpn_id: str) -> dict[str, Any]:
        vpn = self._vpn(vpn_id)
        self.refresh()
        return self._describe(vpn)

    def log_tail(self, vpn_id: str, lines: int = 50) -> list[str]:
        self._vpn(vpn_id)
        return [st.strip_timestamp(line) for line in st.read_log_tail(self.state_dir, vpn_id, lines)]

    def _describe(self, vpn: VpnDef) -> dict[str, Any]:
        s = self.state_of(vpn.id)
        routes_count = 0
        if s.state == CONNECTED and s.interface:
            routes_count = self._route_counter(s.interface)
        return {
            **vpn.public(),
            "state": s.state,
            "interface": s.interface,
            "ip": s.ip,
            "routes_count": routes_count,
            "message": s.message,
            "since": s.since,
        }

    # ----- workers -------------------------------------------------------

    def _connect_worker(self, vpn: VpnDef) -> None:
        try:
            vpn = self._ensure_servercert(vpn)
            self._clear_files(vpn.id)
            result = self.runner.connect(vpn)
            if result.returncode != 0:
                self._clear_files(vpn.id)
                message = self._failure_message(vpn.id, result.output)
                self._set(
                    vpn.id,
                    ERROR,
                    message=message or f"openconnect exited with status {result.returncode}",
                )
                return
            self._wait_for_tunnel(vpn.id)
        except HelperUnavailable:
            self._set(vpn.id, ERROR, message=HELPER_HINT)
        except TunnelError as exc:
            self._set(vpn.id, ERROR, message=str(exc))
        except Exception as exc:  # noqa: BLE001 - a worker must always leave a visible state
            self._set(vpn.id, ERROR, message=f"{type(exc).__name__}: {exc}")

    def _wait_for_tunnel(self, vpn_id: str) -> None:
        deadline = self._clock() + self.connect_timeout
        while True:
            pid = st.read_pid(self.state_dir, vpn_id)
            if pid is not None and self._pid_alive(pid):
                iface = st.read_iface(self.state_dir, vpn_id)
                if iface is not None:
                    self._set(vpn_id, CONNECTED, interface=iface[0], ip=iface[1])
                    return
            elif pid is not None:
                self._clear_files(vpn_id)
                message = self._failure_message(vpn_id)
                self._set(
                    vpn_id, ERROR, message=message or "openconnect exited before the tunnel came up"
                )
                return
            if self._clock() >= deadline:
                break
            self._sleep(self.poll_interval)
        self.runner.disconnect(vpn_id)
        self._clear_files(vpn_id)
        detail = self._failure_message(vpn_id) or "no output from openconnect"
        self._set(vpn_id, ERROR, message=f"timed out after {int(self.connect_timeout)} s: {detail}")

    def _disconnect_worker(self, vpn_id: str) -> None:
        try:
            self.runner.disconnect(vpn_id)
            self._clear_files(vpn_id)
            self._set(vpn_id, DISCONNECTED)
        except HelperUnavailable:
            self._set(vpn_id, ERROR, message=HELPER_HINT)
        except Exception as exc:  # noqa: BLE001
            self._set(vpn_id, ERROR, message=f"{type(exc).__name__}: {exc}")

    def _failure_message(self, vpn_id: str, extra: str = "") -> str | None:
        log = "\n".join(st.read_log_tail(self.state_dir, vpn_id))
        message = st.parse_failure(log) or (extra.strip() or None)
        if message and "failed verification" in message:
            message = f"{message} {PIN_HINT}"
        return message

    def _ensure_servercert(self, vpn: VpnDef) -> VpnDef:
        """Probe the certificate pin once and store it in vpns.yaml."""
        if vpn.servercert:
            return vpn
        output = self.runner.probe(vpn).output
        pin = st.pin_from_probe(output)
        if pin is None:
            if not st.server_reachable(output):
                detail = st.parse_failure(output) or "no output"
                raise TunnelError(f"could not probe server certificate: {detail}")
            pin = TRUSTED_CA
        save_servercert(self.registry.path, vpn.id, pin)
        updated = dataclasses.replace(vpn, servercert=pin)
        with self._lock:
            self.registry.vpns = [updated if v.id == vpn.id else v for v in self.registry.vpns]
        return updated
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tunnel.py -q`
Expected: all pass.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add app/services/tunnel.py tests/fakes.py tests/test_tunnel.py
git commit -m "feat: tunnel manager state machine

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Flask app factory, config, health and Swagger docs

**Files:**
- Create: `app/config.py`, `app/docs.py`, `app/api/__init__.py`, `app/api/health.py`
- Modify: `app/__init__.py` (was empty)
- Create: `tests/conftest.py`
- Test: `tests/test_config.py`, `tests/test_health.py`, `tests/test_docs.py`

**Interfaces:**
- Consumes: `load_registry`, `SudoHelperRunner`, `TunnelManager`.
- Produces: `create_app(config_class=Config, manager: TunnelManager | None = None) -> Flask`; `app.extensions["tunnels"]` holds the manager; `Config` with `SECRET_KEY, VPNS_FILE, STATE_DIR, HELPER, CONNECT_TIMEOUT, DISCONNECT_GRACE`; `env_int(name, default)`; `api_bp` blueprint; `init_docs(app)`; fixtures `state_dir, vpns_file, runner, clock, manager, app, client`.

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py`:

```python
import pytest

from app import create_app
from app.config import Config
from app.services.registry import load_registry
from app.services.tunnel import TunnelManager
from tests.fakes import ENV, VPNS_YAML, FakeClock, FakeRunner, ImmediateThread


@pytest.fixture
def state_dir(tmp_path):
    path = tmp_path / "state"
    path.mkdir()
    return path


@pytest.fixture
def vpns_file(tmp_path):
    path = tmp_path / "vpns.yaml"
    path.write_text(VPNS_YAML)
    return path


@pytest.fixture
def runner(state_dir):
    return FakeRunner(state_dir)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def manager(vpns_file, runner, state_dir, clock):
    return TunnelManager(
        load_registry(vpns_file, ENV),
        runner,
        state_dir,
        connect_timeout=30,
        poll_interval=0.5,
        pid_alive=lambda pid: pid in runner.alive,
        route_counter=lambda iface: 3,
        thread_factory=ImmediateThread,
        clock=clock,
        sleep=clock.sleep,
    )


@pytest.fixture
def app(manager):
    class TestConfig(Config):
        TESTING = True
        SECRET_KEY = "test-secret"

    return create_app(TestConfig, manager=manager)


@pytest.fixture
def client(app):
    return app.test_client()
```

`tests/test_config.py`:

```python
from pathlib import Path

import pytest

from app.config import Config, env_int

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_paths_default_to_the_repository():
    assert Path(Config.VPNS_FILE) == REPO_ROOT / "vpns.yaml"
    assert Path(Config.STATE_DIR) == REPO_ROOT / "state"


def test_helper_and_timeouts_have_defaults():
    assert Config.HELPER == "/usr/local/libexec/vpnconnect/vpnconnect-helper"
    assert Config.CONNECT_TIMEOUT == 30
    assert Config.DISCONNECT_GRACE == 5


@pytest.mark.parametrize(("value", "expected"), [("12", 12), (" 7 ", 7), ("", 30), (None, 30)])
def test_env_int(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("SOME_INT", raising=False)
    else:
        monkeypatch.setenv("SOME_INT", value)
    assert env_int("SOME_INT", 30) == expected
```

`tests/test_health.py`:

```python
def test_health_returns_ok(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_manager_is_attached_to_the_app(app, manager):
    assert app.extensions["tunnels"] is manager
```

`tests/test_docs.py`:

```python
import pytest


@pytest.fixture
def spec(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    return response.get_json()


def test_swagger_ui_is_served_at_docs(client):
    response = client.get("/docs/")

    assert response.status_code == 200
    assert b"swagger" in response.data.lower()
    assert b"<title>vpnconnect API</title>" in response.data


def test_openapi_spec_describes_the_api(spec):
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "vpnconnect API"
    assert spec["info"]["version"]


def test_openapi_spec_documents_health_endpoint(spec):
    assert "200" in spec["paths"]["/api/v1/health"]["get"]["responses"]


def test_openapi_spec_defines_vpn_status_schema(spec):
    props = spec["components"]["schemas"]["VpnStatus"]["properties"]

    assert set(props) == {
        "id",
        "name",
        "server",
        "authgroup",
        "protocol",
        "routes",
        "servercert",
        "has_credentials",
        "missing_credentials",
        "state",
        "interface",
        "ip",
        "routes_count",
        "message",
        "since",
    }
    assert props["state"]["enum"] == [
        "disconnected",
        "connecting",
        "connected",
        "disconnecting",
        "error",
    ]
    assert {"VpnStatus", "ActionAccepted", "BulkResult", "LogTail", "Error"} <= set(
        spec["components"]["schemas"]
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_health.py tests/test_docs.py -q`
Expected: `ImportError: cannot import name 'create_app' from 'app'` (or `No module named 'app.config'`).

- [ ] **Step 3: Implement config, docs, blueprint, health and factory**

`app/config.py`:

```python
import os
from pathlib import Path

# The repository root is two levels above this file: app/config.py -> app -> repo.
REPO_ROOT = Path(__file__).resolve().parents[1]


def env_int(name: str, default: int) -> int:
    """Read an integer from the environment; unset or blank gives the default."""
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    # VPN definitions without secrets. Credentials come from VPN_<ID>_USERNAME / _PASSWORD.
    VPNS_FILE = os.environ.get("VPNCONNECT_VPNS_FILE", str(REPO_ROOT / "vpns.yaml"))
    # Per-tunnel pid, log and iface files. Git-ignored.
    STATE_DIR = os.environ.get("VPNCONNECT_STATE_DIR", str(REPO_ROOT / "state"))
    # Root-owned helper installed once by scripts/setup-privileges.sh.
    HELPER = os.environ.get(
        "VPNCONNECT_HELPER", "/usr/local/libexec/vpnconnect/vpnconnect-helper"
    )
    # Seconds to wait for the tunnel interface after openconnect starts.
    CONNECT_TIMEOUT = env_int("VPNCONNECT_CONNECT_TIMEOUT", 30)
    # Seconds between SIGTERM and SIGKILL when disconnecting.
    DISCONNECT_GRACE = env_int("VPNCONNECT_DISCONNECT_GRACE", 5)
```

`app/docs.py`:

```python
"""OpenAPI documentation served with Swagger UI.

Each route documents itself with a YAML block in its docstring, after a
``---`` line. flasgger collects those blocks into one OpenAPI 3 spec.

- Swagger UI:   /docs/
- Raw spec:     /openapi.json
"""

from flasgger import Swagger
from flask import Flask

API_VERSION = "0.1.0"

SWAGGER_CONFIG = {
    "headers": [],
    "openapi": "3.0.3",
    "specs": [
        {
            "endpoint": "openapi",
            "route": "/openapi.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/docs/",
    "title": "vpnconnect API",
}

STATES = ["disconnected", "connecting", "connected", "disconnecting", "error"]

SWAGGER_TEMPLATE = {
    "info": {
        "title": "vpnconnect API",
        "description": (
            "Start and stop several Cisco AnyConnect VPNs at once with openconnect. "
            "All endpoints live under `/api/v1`."
        ),
        "version": API_VERSION,
    },
    "tags": [
        {"name": "Health", "description": "Service status"},
        {"name": "VPNs", "description": "List, connect and disconnect VPN tunnels"},
    ],
    "components": {
        "schemas": {
            "VpnStatus": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "example": "mininfra"},
                    "name": {"type": "string", "example": "MININFRA office"},
                    "server": {"type": "string", "example": "vpn.example.gov.rw"},
                    "authgroup": {"type": "string", "example": "Staff"},
                    "protocol": {"type": "string", "example": "anyconnect"},
                    "routes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "example": ["10.10.0.0/16"],
                        "description": "Networks forced into the tunnel when the server pushes a full tunnel.",
                    },
                    "servercert": {
                        "type": "string",
                        "nullable": True,
                        "example": "pin-sha256:0Yl6a3cSB6AQ8r5k7fT9m6yLxWvqNzR2pCd3eF4gH5I=",
                        "description": "Stored certificate pin, 'trusted-ca', or null before the first probe.",
                    },
                    "has_credentials": {"type": "boolean"},
                    "missing_credentials": {
                        "type": "array",
                        "items": {"type": "string"},
                        "example": ["VPN_MININFRA_PASSWORD"],
                    },
                    "state": {"type": "string", "enum": STATES},
                    "interface": {"type": "string", "nullable": True, "example": "utun7"},
                    "ip": {"type": "string", "nullable": True, "example": "10.10.47.29"},
                    "routes_count": {"type": "integer", "example": 6},
                    "message": {"type": "string", "nullable": True, "example": "Login failed."},
                    "since": {
                        "type": "string",
                        "nullable": True,
                        "example": "2026-09-17T12:00:00+00:00",
                    },
                },
            },
            "ActionAccepted": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "example": "mininfra"},
                    "state": {"type": "string", "example": "connecting"},
                },
            },
            "BulkResult": {
                "type": "object",
                "properties": {
                    "started": {"type": "array", "items": {"type": "string"}},
                    "skipped": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "LogTail": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "lines": {"type": "array", "items": {"type": "string"}},
                },
            },
            "Error": {
                "type": "object",
                "properties": {"error": {"type": "string", "example": "unknown vpn: ghost"}},
            },
        }
    },
}


def init_docs(app: Flask) -> Swagger:
    """Attach Swagger UI and the OpenAPI spec routes to the app."""
    return Swagger(app, config=SWAGGER_CONFIG, template=SWAGGER_TEMPLATE)
```

`app/api/__init__.py`:

```python
from flask import Blueprint

api_bp = Blueprint("api", __name__)

from app.api import health  # noqa: E402, F401
```

`app/api/health.py`:

```python
from app.api import api_bp


@api_bp.get("/health")
def health():
    """Liveness check.
    ---
    tags:
      - Health
    responses:
      200:
        description: The API is up.
        content:
          application/json:
            schema:
              type: object
              properties:
                status:
                  type: string
                  example: ok
    """
    return {"status": "ok"}
```

`app/__init__.py`:

```python
import os
from pathlib import Path

from flask import Flask

from app.config import Config
from app.docs import init_docs
from app.services.registry import load_registry
from app.services.runner import SudoHelperRunner
from app.services.tunnel import TunnelManager


def build_manager(config: dict) -> TunnelManager:
    """The real manager: vpns.yaml + environment credentials + sudo helper."""
    registry = load_registry(config["VPNS_FILE"], os.environ)
    runner = SudoHelperRunner(
        config["HELPER"],
        connect_timeout=config["CONNECT_TIMEOUT"],
        disconnect_grace=config["DISCONNECT_GRACE"],
    )
    return TunnelManager(
        registry, runner, Path(config["STATE_DIR"]), connect_timeout=config["CONNECT_TIMEOUT"]
    )


def create_app(config_class: type[Config] = Config, manager: TunnelManager | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    # A bad vpns.yaml raises RegistryError here, so `flask run` fails loudly
    # with the offending entry named instead of serving an empty dashboard.
    app.extensions["tunnels"] = manager if manager is not None else build_manager(app.config)

    from app.api import api_bp

    app.register_blueprint(api_bp, url_prefix="/api/v1")
    init_docs(app)

    return app
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass (registry, status, runner, tunnel, config, health, docs).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add app tests
git commit -m "feat: Flask app factory with health endpoint and Swagger docs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: VPN API endpoints

**Files:**
- Create: `app/api/vpns.py`
- Modify: `app/api/__init__.py` (import `vpns`)
- Test: `tests/test_vpns_api.py`, extend `tests/test_docs.py`

**Interfaces:**
- Consumes: `TunnelManager` methods and exceptions (Task 4) via `current_app.extensions["tunnels"]`.
- Produces: the routes in the table below, all JSON. Errors are `{"error": "<message>"}`.

| Method | Path | Success | Errors |
|---|---|---|---|
| GET | `/api/v1/vpns` | 200 `{"vpns": [VpnStatus]}` | |
| GET | `/api/v1/vpns/<vpn_id>` | 200 `VpnStatus` | 404 |
| POST | `/api/v1/vpns/<vpn_id>/connect` | 202 `{"id","state":"connecting"}` | 400 missing credentials, 404, 409 |
| POST | `/api/v1/vpns/<vpn_id>/disconnect` | 202 `{"id","state":"disconnecting"}` | 404, 409 |
| POST | `/api/v1/vpns/connect-all` | 202 `BulkResult` | |
| POST | `/api/v1/vpns/disconnect-all` | 202 `BulkResult` | |
| GET | `/api/v1/vpns/<vpn_id>/log?lines=50` | 200 `{"id","lines"}` | 404 |

- [ ] **Step 1: Write the failing tests**

`tests/test_vpns_api.py`:

```python
import dataclasses


def _drop_password(manager, vpn_id):
    manager.registry.vpns = [
        dataclasses.replace(v, password=None) if v.id == vpn_id else v for v in manager.registry.vpns
    ]


def test_list_vpns_returns_public_fields_and_state(client):
    response = client.get("/api/v1/vpns")

    assert response.status_code == 200
    vpns = response.get_json()["vpns"]
    assert [v["id"] for v in vpns] == ["mininfra", "rica"]
    assert vpns[0]["state"] == "disconnected"
    assert vpns[0]["has_credentials"] is True
    assert vpns[0]["routes"] == ["10.10.0.0/16"]
    for vpn in vpns:
        assert "password" not in vpn
        assert "username" not in vpn


def test_get_single_vpn_and_unknown(client):
    assert client.get("/api/v1/vpns/mininfra").get_json()["id"] == "mininfra"

    response = client.get("/api/v1/vpns/ghost")
    assert response.status_code == 404
    assert response.get_json() == {"error": "unknown vpn: ghost"}


def test_connect_returns_202_and_row_becomes_connected(client):
    response = client.post("/api/v1/vpns/mininfra/connect")

    assert response.status_code == 202
    assert response.get_json() == {"id": "mininfra", "state": "connecting"}
    row = client.get("/api/v1/vpns/mininfra").get_json()
    assert row["state"] == "connected"
    assert row["interface"] == "utun9"
    assert row["ip"] == "10.9.9.9"
    assert row["routes_count"] == 3


def test_connect_conflict_and_unknown(client):
    client.post("/api/v1/vpns/mininfra/connect")

    response = client.post("/api/v1/vpns/mininfra/connect")
    assert response.status_code == 409
    assert response.get_json() == {"error": "mininfra is already connected"}
    assert client.post("/api/v1/vpns/ghost/connect").status_code == 404


def test_connect_without_credentials_is_400(client, manager):
    _drop_password(manager, "rica")

    response = client.post("/api/v1/vpns/rica/connect")

    assert response.status_code == 400
    assert response.get_json() == {"error": "missing credentials: VPN_RICA_PASSWORD"}


def test_error_state_is_visible(client, runner):
    runner.connect_mode = "login-failed"

    client.post("/api/v1/vpns/mininfra/connect")

    row = client.get("/api/v1/vpns/mininfra").get_json()
    assert row["state"] == "error"
    assert row["message"] == "Login failed."


def test_disconnect_flow(client):
    response = client.post("/api/v1/vpns/mininfra/disconnect")
    assert response.status_code == 409
    assert response.get_json() == {"error": "mininfra is not connected"}

    client.post("/api/v1/vpns/mininfra/connect")
    response = client.post("/api/v1/vpns/mininfra/disconnect")

    assert response.status_code == 202
    assert response.get_json() == {"id": "mininfra", "state": "disconnecting"}
    assert client.get("/api/v1/vpns/mininfra").get_json()["state"] == "disconnected"
    assert client.post("/api/v1/vpns/ghost/disconnect").status_code == 404


def test_connect_all_and_disconnect_all(client, manager):
    _drop_password(manager, "rica")

    response = client.post("/api/v1/vpns/connect-all")
    assert response.status_code == 202
    assert response.get_json() == {
        "started": ["mininfra"],
        "skipped": [{"id": "rica", "reason": "missing credentials: VPN_RICA_PASSWORD"}],
    }

    response = client.post("/api/v1/vpns/disconnect-all")
    assert response.status_code == 202
    assert response.get_json() == {
        "started": ["mininfra"],
        "skipped": [{"id": "rica", "reason": "rica is not connected"}],
    }


def test_log_endpoint(client, state_dir):
    client.post("/api/v1/vpns/mininfra/connect")
    (state_dir / "mininfra.log").write_text("[t] one\n[t] two\n[t] three\n")

    response = client.get("/api/v1/vpns/mininfra/log?lines=2")

    assert response.status_code == 200
    assert response.get_json() == {"id": "mininfra", "lines": ["two", "three"]}
    assert client.get("/api/v1/vpns/mininfra/log").get_json()["lines"] == ["one", "two", "three"]
    assert client.get("/api/v1/vpns/ghost/log").status_code == 404


def test_log_lines_parameter_is_clamped(client, state_dir):
    (state_dir / "mininfra.log").write_text("\n".join(str(i) for i in range(600)) + "\n")

    assert len(client.get("/api/v1/vpns/mininfra/log?lines=0").get_json()["lines"]) == 1
    assert len(client.get("/api/v1/vpns/mininfra/log?lines=9999").get_json()["lines"]) == 500
    assert len(client.get("/api/v1/vpns/mininfra/log?lines=abc").get_json()["lines"]) == 50
```

Append to `tests/test_docs.py`:

```python
def test_openapi_spec_documents_every_vpn_endpoint(spec):
    paths = spec["paths"]

    assert "200" in paths["/api/v1/vpns"]["get"]["responses"]
    assert set(paths["/api/v1/vpns/{vpn_id}"]["get"]["responses"]) == {"200", "404"}
    assert set(paths["/api/v1/vpns/{vpn_id}/connect"]["post"]["responses"]) == {
        "202",
        "400",
        "404",
        "409",
    }
    assert set(paths["/api/v1/vpns/{vpn_id}/disconnect"]["post"]["responses"]) == {
        "202",
        "404",
        "409",
    }
    assert "202" in paths["/api/v1/vpns/connect-all"]["post"]["responses"]
    assert "202" in paths["/api/v1/vpns/disconnect-all"]["post"]["responses"]
    assert set(paths["/api/v1/vpns/{vpn_id}/log"]["get"]["responses"]) == {"200", "404"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_vpns_api.py tests/test_docs.py -q`
Expected: 404s everywhere (`assert 404 == 200`) and a KeyError on `/api/v1/vpns` in the spec.

- [ ] **Step 3: Implement the endpoints**

`app/api/vpns.py`:

```python
"""VPN endpoints. Actions return 202 right away; poll GET /vpns for progress."""

from flask import current_app, jsonify, request

from app.api import api_bp
from app.services.tunnel import (
    InvalidTransition,
    MissingCredentials,
    TunnelManager,
    UnknownVpn,
)

MAX_LOG_LINES = 500


def manager() -> TunnelManager:
    return current_app.extensions["tunnels"]


def error(message: str, status: int):
    return jsonify({"error": message}), status


@api_bp.get("/vpns")
def list_vpns():
    """List every VPN with its live status.
    ---
    tags:
      - VPNs
    responses:
      200:
        description: All VPNs from vpns.yaml with current tunnel state.
        content:
          application/json:
            schema:
              type: object
              properties:
                vpns:
                  type: array
                  items:
                    $ref: '#/components/schemas/VpnStatus'
    """
    return jsonify({"vpns": manager().snapshot()})


@api_bp.get("/vpns/<vpn_id>")
def get_vpn(vpn_id: str):
    """Status of one VPN.
    ---
    tags:
      - VPNs
    parameters:
      - in: path
        name: vpn_id
        required: true
        schema:
          type: string
    responses:
      200:
        description: The VPN and its tunnel state.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/VpnStatus'
      404:
        description: Unknown VPN id.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Error'
    """
    try:
        return jsonify(manager().snapshot_one(vpn_id))
    except UnknownVpn as exc:
        return error(str(exc), 404)


@api_bp.post("/vpns/<vpn_id>/connect")
def connect_vpn(vpn_id: str):
    """Start connecting one VPN in the background.
    ---
    tags:
      - VPNs
    parameters:
      - in: path
        name: vpn_id
        required: true
        schema:
          type: string
    responses:
      202:
        description: Connection started; poll GET /vpns/{vpn_id}.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ActionAccepted'
      400:
        description: Username or password missing from .env.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Error'
      404:
        description: Unknown VPN id.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Error'
      409:
        description: Already connecting, connected or disconnecting.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Error'
    """
    try:
        manager().connect(vpn_id)
    except UnknownVpn as exc:
        return error(str(exc), 404)
    except MissingCredentials as exc:
        return error(str(exc), 400)
    except InvalidTransition as exc:
        return error(str(exc), 409)
    return jsonify({"id": vpn_id, "state": "connecting"}), 202


@api_bp.post("/vpns/<vpn_id>/disconnect")
def disconnect_vpn(vpn_id: str):
    """Stop one VPN in the background.
    ---
    tags:
      - VPNs
    parameters:
      - in: path
        name: vpn_id
        required: true
        schema:
          type: string
    responses:
      202:
        description: Disconnect started; poll GET /vpns/{vpn_id}.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ActionAccepted'
      404:
        description: Unknown VPN id.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Error'
      409:
        description: Not connected, or a connect/disconnect is still running.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Error'
    """
    try:
        manager().disconnect(vpn_id)
    except UnknownVpn as exc:
        return error(str(exc), 404)
    except InvalidTransition as exc:
        return error(str(exc), 409)
    return jsonify({"id": vpn_id, "state": "disconnecting"}), 202


@api_bp.post("/vpns/connect-all")
def connect_all():
    """Connect every VPN that has credentials and is not already up.
    ---
    tags:
      - VPNs
    responses:
      202:
        description: Ids that started connecting and ids that were skipped, with reasons.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/BulkResult'
    """
    return jsonify(manager().connect_all()), 202


@api_bp.post("/vpns/disconnect-all")
def disconnect_all():
    """Disconnect every VPN that is connected or in error.
    ---
    tags:
      - VPNs
    responses:
      202:
        description: Ids that started disconnecting and ids that were skipped, with reasons.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/BulkResult'
    """
    return jsonify(manager().disconnect_all()), 202


@api_bp.get("/vpns/<vpn_id>/log")
def vpn_log(vpn_id: str):
    """Last lines of openconnect's log for one VPN.
    ---
    tags:
      - VPNs
    parameters:
      - in: path
        name: vpn_id
        required: true
        schema:
          type: string
      - in: query
        name: lines
        required: false
        schema:
          type: integer
          default: 50
          minimum: 1
          maximum: 500
    responses:
      200:
        description: Log lines, oldest first, timestamps removed.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/LogTail'
      404:
        description: Unknown VPN id.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Error'
    """
    lines = request.args.get("lines", default=50, type=int)
    if lines is None:
        lines = 50
    lines = max(1, min(lines, MAX_LOG_LINES))
    try:
        return jsonify({"id": vpn_id, "lines": manager().log_tail(vpn_id, lines)})
    except UnknownVpn as exc:
        return error(str(exc), 404)
```

Update `app/api/__init__.py`:

```python
from flask import Blueprint

api_bp = Blueprint("api", __name__)

from app.api import health, vpns  # noqa: E402, F401
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add app/api tests/test_vpns_api.py tests/test_docs.py
git commit -m "feat: VPN connect, disconnect, bulk and log endpoints

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Dashboard page

**Files:**
- Create: `app/templates/index.html`
- Modify: `app/__init__.py` (add `GET /` route)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: the JSON API from Task 6.
- Produces: `GET /` returning the page.

- [ ] **Step 1: Write the failing test**

`tests/test_dashboard.py`:

```python
def test_dashboard_is_served_at_root(client):
    response = client.get("/")

    assert response.status_code == 200
    html = response.data.decode()
    assert "<title>vpnconnect</title>" in html
    assert 'id="connect-all"' in html
    assert 'id="disconnect-all"' in html
    assert 'id="rows"' in html
    assert '"/api/v1/vpns"' in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_dashboard.py -q`
Expected: `assert 404 == 200`.

- [ ] **Step 3: Add the route and the page**

In `app/__init__.py`, add `render_template` to the flask import and, after `init_docs(app)`:

```python
    @app.get("/")
    def index():
        return render_template("index.html")
```

`app/templates/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>vpnconnect</title>
<style>
  :root {
    --bg: #f6f7f9; --card: #ffffff; --text: #1f2937; --muted: #6b7280; --border: #e5e7eb;
    --accent: #1d4ed8; --green: #15803d; --green-bg: #dcfce7; --amber: #b45309;
    --amber-bg: #fef3c7; --red: #b91c1c; --red-bg: #fee2e2; --grey: #374151; --grey-bg: #e5e7eb;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: var(--bg); color: var(--text); }
  main { max-width: 1040px; margin: 0 auto; padding: 24px 16px; }
  header { display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
           justify-content: space-between; margin-bottom: 16px; }
  h1 { font-size: 22px; margin: 0; }
  .muted { color: var(--muted); font-size: 13px; }
  button { font: inherit; padding: 6px 12px; border-radius: 6px; border: 1px solid var(--border);
           background: var(--card); cursor: pointer; }
  button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  button:disabled { opacity: .45; cursor: not-allowed; }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; background: var(--card);
          border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border);
           vertical-align: top; }
  th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted);
       background: #fafafa; }
  tr:last-child td { border-bottom: none; }
  .pill { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px;
          font-weight: 600; }
  .pill.disconnected { background: var(--grey-bg); color: var(--grey); }
  .pill.connecting, .pill.disconnecting { background: var(--amber-bg); color: var(--amber); }
  .pill.connected { background: var(--green-bg); color: var(--green); }
  .pill.error { background: var(--red-bg); color: var(--red); }
  .msg { font-size: 13px; color: var(--muted); margin-top: 4px; max-width: 420px;
         word-break: break-word; }
  .msg.error { color: var(--red); }
  code { font-size: 13px; }
  pre.log { margin: 0; padding: 10px 12px; background: #111827; color: #e5e7eb; font-size: 12px;
            max-height: 260px; overflow: auto; white-space: pre-wrap; }
  .actions { display: flex; gap: 6px; flex-wrap: wrap; }
  #toast { position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%);
           background: #111827; color: #fff; padding: 8px 14px; border-radius: 6px;
           font-size: 13px; opacity: 0; transition: opacity .2s; pointer-events: none; }
  #toast.show { opacity: 1; }
</style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>vpnconnect</h1>
      <div class="muted">Several Cisco AnyConnect tunnels at once, split-routed.
        <span id="refreshed"></span></div>
    </div>
    <div class="actions">
      <button id="connect-all" class="primary">Connect all</button>
      <button id="disconnect-all">Disconnect all</button>
    </div>
  </header>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>VPN</th><th>Status</th><th>Interface</th><th>IP</th><th>Routes</th><th>Actions</th></tr>
      </thead>
      <tbody id="rows"><tr><td colspan="6" class="muted">Loading…</td></tr></tbody>
    </table>
  </div>
</main>
<div id="toast"></div>
<script>
const API = "/api/v1/vpns";
const openLogs = new Set();
const logs = {};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function toast(text) {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2500);
}

async function api(method, path) {
  const res = await fetch(path, { method });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}

function row(v) {
  const busy = v.state === "connecting" || v.state === "disconnecting";
  const canConnect = v.has_credentials && (v.state === "disconnected" || v.state === "error");
  const canDisconnect = v.state === "connected" || v.state === "error";
  const creds = v.has_credentials
    ? ""
    : `<div class="msg error">Missing ${esc(v.missing_credentials.join(", "))} in .env</div>`;
  const msg = v.message
    ? `<div class="msg ${v.state === "error" ? "error" : ""}">${esc(v.message)}</div>`
    : "";
  const logOpen = openLogs.has(v.id);
  const logRow = logOpen
    ? `<tr><td colspan="6"><pre class="log">${esc((logs[v.id] || []).join("\n") || "(empty)")}</pre></td></tr>`
    : "";
  return `
    <tr>
      <td><strong>${esc(v.name)}</strong>
          <div class="muted"><code>${esc(v.server)}</code> · ${esc(v.authgroup)}</div>${creds}</td>
      <td><span class="pill ${esc(v.state)}">${esc(v.state)}</span>${msg}</td>
      <td><code>${esc(v.interface || "–")}</code></td>
      <td><code>${esc(v.ip || "–")}</code></td>
      <td>${v.state === "connected" ? v.routes_count : "–"}</td>
      <td class="actions">
        <button data-act="connect" data-id="${esc(v.id)}" ${canConnect && !busy ? "" : "disabled"}>Connect</button>
        <button data-act="disconnect" data-id="${esc(v.id)}" ${canDisconnect && !busy ? "" : "disabled"}>Disconnect</button>
        <button data-act="log" data-id="${esc(v.id)}">${logOpen ? "Hide log" : "Log"}</button>
      </td>
    </tr>${logRow}`;
}

async function refresh() {
  try {
    const { vpns } = await api("GET", API);
    await Promise.all([...openLogs].map(async (id) => {
      logs[id] = (await api("GET", `${API}/${id}/log?lines=50`)).lines;
    }));
    document.getElementById("rows").innerHTML = vpns.map(row).join("")
      || `<tr><td colspan="6" class="muted">No VPNs defined in vpns.yaml</td></tr>`;
    document.getElementById("refreshed").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    toast(`Refresh failed: ${err.message}`);
  }
}

document.getElementById("rows").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const { act, id } = btn.dataset;
  if (act === "log") {
    if (openLogs.has(id)) openLogs.delete(id); else openLogs.add(id);
    return refresh();
  }
  btn.disabled = true;
  try { await api("POST", `${API}/${id}/${act}`); } catch (err) { toast(err.message); }
  refresh();
});

document.getElementById("connect-all").addEventListener("click", async () => {
  try {
    const r = await api("POST", `${API}/connect-all`);
    toast(`Connecting ${r.started.length}, skipped ${r.skipped.length}`);
  } catch (err) { toast(err.message); }
  refresh();
});

document.getElementById("disconnect-all").addEventListener("click", async () => {
  try {
    const r = await api("POST", `${API}/disconnect-all`);
    toast(`Disconnecting ${r.started.length}`);
  } catch (err) { toast(err.message); }
  refresh();
});

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add app/__init__.py app/templates/index.html tests/test_dashboard.py
git commit -m "feat: dashboard page

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Split-routing wrapper script

**Files:**
- Create: `scripts/vpnc-split.sh`
- Test: `tests/test_scripts.py` (wrapper half)

**Interfaces:**
- Consumes (environment set by openconnect): `reason` (`pre-init|connect|reconnect|disconnect|attempt-reconnect`), `TUNDEV`, `INTERNAL_IP4_ADDRESS`, `CISCO_SPLIT_INC`, `CISCO_SPLIT_INC_<i>_{ADDR,MASK,MASKLEN}`. Environment set by the helper: `VPNCONNECT_ID`, `VPNCONNECT_STATE_DIR`, `VPNCONNECT_ROUTES` (`addr:mask:len,...` or empty).
- Produces: the placeholder `__VPNC_SCRIPT__` (replaced at install time); the file `$VPNCONNECT_STATE_DIR/$VPNCONNECT_ID.iface` containing `"<TUNDEV> <INTERNAL_IP4_ADDRESS>\n"`, present only while connected.

- [ ] **Step 1: Write the failing tests**

`tests/test_scripts.py` (first half; Task 9 appends the helper tests):

```python
"""Run the bash scripts against stubs. Nothing here needs root or a network."""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

STUB_VPNC_SCRIPT = """#!/bin/bash
env | sort > "$STUB_OUT"
echo "STUB_ARGS=$*" >> "$STUB_OUT"
exit "${STUB_EXIT:-0}"
"""


@pytest.fixture
def split(tmp_path):
    """vpnc-split.sh installed into tmp against a stub vpnc-script that records its environment."""
    stub = tmp_path / "vpnc-script"
    stub.write_text(STUB_VPNC_SCRIPT)
    stub.chmod(0o755)
    wrapper = tmp_path / "vpnc-split.sh"
    wrapper.write_text(
        (SCRIPTS / "vpnc-split.sh").read_text().replace("__VPNC_SCRIPT__", str(stub))
    )
    wrapper.chmod(0o755)
    return wrapper


def run_split(wrapper, tmp_path, extra_env, args=()):
    out = tmp_path / "stub.out"
    out.unlink(missing_ok=True)
    env = {"PATH": os.environ["PATH"], "STUB_OUT": str(out), **extra_env}
    proc = subprocess.run([str(wrapper), *args], env=env, capture_output=True, text=True)
    recorded = {}
    if out.exists():
        for line in out.read_text().splitlines():
            key, _, value = line.partition("=")
            recorded[key] = value
    return proc, recorded


def connect_env(tmp_path, **overrides):
    env = {
        "reason": "connect",
        "TUNDEV": "utun9",
        "INTERNAL_IP4_ADDRESS": "10.9.9.9",
        "VPNCONNECT_ID": "x",
        "VPNCONNECT_STATE_DIR": str(tmp_path),
        "VPNCONNECT_ROUTES": "",
    }
    env.update(overrides)
    return env


def test_server_split_routes_are_left_alone(split, tmp_path):
    proc, env = run_split(
        split,
        tmp_path,
        connect_env(
            tmp_path,
            CISCO_SPLIT_INC="2",
            CISCO_SPLIT_INC_0_ADDR="10.1.0.0",
            VPNCONNECT_ROUTES="10.10.0.0:255.255.0.0:16",
        ),
    )

    assert proc.returncode == 0, proc.stderr
    assert env["CISCO_SPLIT_INC"] == "2"
    assert env["CISCO_SPLIT_INC_0_ADDR"] == "10.1.0.0"
    assert "CISCO_SPLIT_INC_0_MASK" not in env


def test_full_tunnel_with_configured_routes_is_forced_to_split(split, tmp_path):
    routes = "10.10.0.0:255.255.0.0:16,10.20.5.0:255.255.255.0:24"

    proc, env = run_split(split, tmp_path, connect_env(tmp_path, VPNCONNECT_ROUTES=routes))

    assert proc.returncode == 0, proc.stderr
    assert env["CISCO_SPLIT_INC"] == "2"
    assert (env["CISCO_SPLIT_INC_0_ADDR"], env["CISCO_SPLIT_INC_0_MASK"], env["CISCO_SPLIT_INC_0_MASKLEN"]) == (
        "10.10.0.0",
        "255.255.0.0",
        "16",
    )
    assert (env["CISCO_SPLIT_INC_1_ADDR"], env["CISCO_SPLIT_INC_1_MASK"], env["CISCO_SPLIT_INC_1_MASKLEN"]) == (
        "10.20.5.0",
        "255.255.255.0",
        "24",
    )
    assert env["CISCO_SPLIT_INC_0_PROTOCOL"] == "0"


def test_full_tunnel_without_routes_is_refused(split, tmp_path):
    proc, env = run_split(split, tmp_path, connect_env(tmp_path))

    assert proc.returncode == 1
    assert "refusing to take the default route" in proc.stderr
    assert env == {}  # vpnc-script never ran
    assert not (tmp_path / "x.iface").exists()


def test_zero_split_count_is_treated_as_full_tunnel(split, tmp_path):
    proc, _ = run_split(split, tmp_path, connect_env(tmp_path, CISCO_SPLIT_INC="0"))

    assert proc.returncode == 1


def test_pre_init_is_passed_through_without_checks(split, tmp_path):
    proc, env = run_split(split, tmp_path, connect_env(tmp_path, reason="pre-init"))

    assert proc.returncode == 0, proc.stderr
    assert env["reason"] == "pre-init"
    assert not (tmp_path / "x.iface").exists()


def test_iface_file_written_on_connect_and_removed_on_disconnect(split, tmp_path):
    common = connect_env(tmp_path, CISCO_SPLIT_INC="1")

    run_split(split, tmp_path, common)
    assert (tmp_path / "x.iface").read_text() == "utun9 10.9.9.9\n"

    run_split(split, tmp_path, {**common, "reason": "disconnect"})
    assert not (tmp_path / "x.iface").exists()


def test_disconnect_reinjects_routes_so_vpnc_script_removes_them(split, tmp_path):
    env_in = connect_env(tmp_path, reason="disconnect", VPNCONNECT_ROUTES="10.10.0.0:255.255.0.0:16")

    proc, env = run_split(split, tmp_path, env_in)

    assert proc.returncode == 0, proc.stderr
    assert env["CISCO_SPLIT_INC"] == "1"
    assert env["CISCO_SPLIT_INC_0_ADDR"] == "10.10.0.0"


def test_arguments_and_exit_code_pass_through_and_failure_records_nothing(split, tmp_path):
    proc, env = run_split(
        split, tmp_path, connect_env(tmp_path, CISCO_SPLIT_INC="1", STUB_EXIT="3"), args=("alpha", "beta")
    )

    assert proc.returncode == 3
    assert env["STUB_ARGS"] == "alpha beta"
    assert not (tmp_path / "x.iface").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scripts.py -q`
Expected: `FileNotFoundError` reading `scripts/vpnc-split.sh`.

- [ ] **Step 3: Write the wrapper**

`scripts/vpnc-split.sh`:

```bash
#!/bin/bash
# vpnconnect wrapper around openconnect's vpnc-script.
#
# openconnect runs this script with the connection described in environment
# variables (reason, TUNDEV, INTERNAL_IP4_ADDRESS, CISCO_SPLIT_INC and
# CISCO_SPLIT_INC_<i>_{ADDR,MASK,MASKLEN}); vpnconnect-helper adds
# VPNCONNECT_ID, VPNCONNECT_STATE_DIR and VPNCONNECT_ROUTES.
#
# 1. Split-routing guarantee. When the server pushes a full tunnel (no
#    CISCO_SPLIT_INC), inject this VPN's configured routes so vpnc-script adds
#    only those and never a default route. No routes configured: refuse.
# 2. Record "<TUNDEV> <IP>" in <state>/<id>.iface after a successful connect
#    so the dashboard can show them; remove the file on disconnect.
#
# Must run on macOS /bin/bash 3.2: no arrays, no mapfile.
set -u

# Replaced by scripts/setup-privileges.sh at install time.
VPNC_SCRIPT="__VPNC_SCRIPT__"

server_sent_split() {
  [ -n "${CISCO_SPLIT_INC:-}" ] && [ "${CISCO_SPLIT_INC}" -ge 1 ] 2>/dev/null
}

inject_routes() {
  local i=0 triple addr rest mask len
  for triple in $(printf '%s' "$VPNCONNECT_ROUTES" | tr ',' ' '); do
    addr=${triple%%:*}
    rest=${triple#*:}
    mask=${rest%%:*}
    len=${rest#*:}
    export "CISCO_SPLIT_INC_${i}_ADDR=$addr"
    export "CISCO_SPLIT_INC_${i}_MASK=$mask"
    export "CISCO_SPLIT_INC_${i}_MASKLEN=$len"
    export "CISCO_SPLIT_INC_${i}_PROTOCOL=0"
    export "CISCO_SPLIT_INC_${i}_SPORT=0"
    export "CISCO_SPLIT_INC_${i}_DPORT=0"
    i=$((i + 1))
  done
  export CISCO_SPLIT_INC=$i
}

case "${reason:-}" in
  connect|reconnect)
    if ! server_sent_split; then
      if [ -n "${VPNCONNECT_ROUTES:-}" ]; then
        inject_routes
      else
        echo "vpnconnect: server pushed a full tunnel and no routes are configured for this VPN; refusing to take the default route" >&2
        exit 1
      fi
    fi
    ;;
  disconnect)
    # Same environment as on connect so vpnc-script removes exactly what it added.
    if ! server_sent_split && [ -n "${VPNCONNECT_ROUTES:-}" ]; then
      inject_routes
    fi
    ;;
esac

"$VPNC_SCRIPT" "$@"
rc=$?

if [ -n "${VPNCONNECT_ID:-}" ] && [ -n "${VPNCONNECT_STATE_DIR:-}" ]; then
  iface_file="$VPNCONNECT_STATE_DIR/$VPNCONNECT_ID.iface"
  case "${reason:-}" in
    connect|reconnect)
      if [ "$rc" -eq 0 ]; then
        printf '%s %s\n' "${TUNDEV:-}" "${INTERNAL_IP4_ADDRESS:-}" > "$iface_file"
      fi
      ;;
    disconnect)
      rm -f "$iface_file"
      ;;
  esac
fi

exit "$rc"
```

Then: `chmod +x scripts/vpnc-split.sh`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scripts.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add scripts/vpnc-split.sh tests/test_scripts.py
git commit -m "feat: split-routing wrapper around vpnc-script

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Privileged helper and one-time setup script

**Files:**
- Create: `scripts/vpnconnect-helper`, `scripts/setup-privileges.sh`
- Test: append to `tests/test_scripts.py`

**Interfaces:**
- Consumes: `scripts/vpnc-split.sh` (Task 8), openconnect at the baked path.
- Produces: placeholders `__STATE_DIR__`, `__LIBEXEC_DIR__`, `__OPENCONNECT__` in the helper; commands exactly as `SudoHelperRunner` (Task 3) calls them: `probe <server> <authgroup> <protocol>`, `connect <id> <server> <authgroup> <protocol> <username> <servercert|trusted-ca> <routes|->` (password on stdin), `disconnect <id> [grace_seconds]`. Exit codes: 64 usage, 65 invalid id or grace, otherwise openconnect's.

- [ ] **Step 1: Write the failing tests**

Add `import time` to the standard-library import block at the top of
`tests/test_scripts.py` and `from app.services import status` after the
`pytest` import (ruff rejects imports placed mid-file), then append:

```python
FAKE_OPENCONNECT = """#!/bin/bash
# Fake openconnect for tests. Understands the flags vpnconnect-helper passes.
#   probe (no --passwd-on-stdin): print a certificate pin, exit 1
#   password "good": background a sleeper named openconnect, write the pid file,
#                    run --script with reason=connect, print the success line, exit 0
#   any other password: "Login failed.", exit 1
pidfile=""; script=""; passwd_on_stdin=0
for arg in "$@"; do
  case "$arg" in
    --pid-file=*) pidfile="${arg#--pid-file=}" ;;
    --script=*) script="${arg#--script=}" ;;
    --passwd-on-stdin) passwd_on_stdin=1 ;;
  esac
done
echo "fake-openconnect: $*"
if [ "$passwd_on_stdin" -eq 0 ]; then
  echo 'Certificate from VPN server "vpn.example" failed verification.'
  echo 'To trust this server in future, perhaps add this to your command line:'
  echo '    --servercert pin-sha256:FAKEPIN0000000000000000000000000000000000000='
  exit 1
fi
read -r password
if [ "$password" != "good" ]; then
  echo "Login failed."
  exit 1
fi
( exec -a openconnect sleep 300 ) &
echo $! > "$pidfile"
if ! reason=connect TUNDEV=utun9 INTERNAL_IP4_ADDRESS=10.9.9.9 CISCO_SPLIT_INC=1 "$script"; then
  echo "Script '$script' returned error 1"
  exit 1
fi
echo "Configured as 10.9.9.9, with SSL connected and DTLS in progress"
echo "Continuing in background; pid $(cat "$pidfile")"
exit 0
"""

CONNECT_ARGS = (
    "connect",
    "mininfra",
    "vpn.example",
    "Staff",
    "anyconnect",
    "longin",
    "pin-sha256:abc=",
    "10.10.0.0:255.255.0.0:16",
)


@pytest.fixture
def helper(tmp_path):
    """The helper installed into tmp exactly as setup-privileges.sh does, against a fake openconnect."""
    libexec = tmp_path / "libexec"
    libexec.mkdir()
    state = tmp_path / "state"

    fake_oc = tmp_path / "openconnect"
    fake_oc.write_text(FAKE_OPENCONNECT)
    fake_oc.chmod(0o755)

    stub_vpnc = tmp_path / "vpnc-script"
    stub_vpnc.write_text("#!/bin/bash\nexit 0\n")
    stub_vpnc.chmod(0o755)

    split = libexec / "vpnc-split.sh"
    split.write_text(
        (SCRIPTS / "vpnc-split.sh").read_text().replace("__VPNC_SCRIPT__", str(stub_vpnc))
    )
    split.chmod(0o755)

    installed = libexec / "vpnconnect-helper"
    installed.write_text(
        (SCRIPTS / "vpnconnect-helper")
        .read_text()
        .replace("__STATE_DIR__", str(state))
        .replace("__LIBEXEC_DIR__", str(libexec))
        .replace("__OPENCONNECT__", str(fake_oc))
    )
    installed.chmod(0o755)

    yield installed, state

    for pidfile in state.glob("*.pid") if state.exists() else []:
        text = pidfile.read_text().strip()
        if text.isdigit():
            try:
                os.kill(int(text), 9)
            except ProcessLookupError:
                pass


def run_helper(installed, *args, stdin=None):
    return subprocess.run([str(installed), *args], input=stdin, capture_output=True, text=True)


def test_helper_probe_prints_openconnect_output(helper):
    installed, _ = helper

    proc = run_helper(installed, "probe", "vpn.example", "Staff", "anyconnect")

    assert proc.returncode == 0, proc.stderr
    assert "pin-sha256:FAKEPIN" in proc.stdout
    assert "--authgroup=Staff" in proc.stdout
    assert "--passwd-on-stdin" not in proc.stdout


def test_helper_connect_success_writes_pid_log_and_iface(helper):
    installed, state = helper

    proc = run_helper(installed, *CONNECT_ARGS, stdin="good\n")

    assert proc.returncode == 0, proc.stderr
    pid = int((state / "mininfra.pid").read_text())
    assert status.pid_alive(pid)
    log = (state / "mininfra.log").read_text()
    assert "--servercert pin-sha256:abc=" in log
    assert "--user=longin" in log
    assert "--authgroup=Staff" in log
    assert "--passwd-on-stdin" in log
    assert "--background" in log
    assert "good" not in log
    assert "Configured as 10.9.9.9" in log
    assert (state / "mininfra.iface").read_text() == "utun9 10.9.9.9\n"


def test_helper_connect_trusted_ca_omits_servercert_flag(helper):
    installed, state = helper
    args = list(CONNECT_ARGS)
    args[6] = "trusted-ca"
    args[7] = "-"

    proc = run_helper(installed, *args, stdin="good\n")

    assert proc.returncode == 0, proc.stderr
    assert "--servercert" not in (state / "mininfra.log").read_text()


def test_helper_connect_wrong_password_returns_nonzero_and_logs(helper):
    installed, state = helper

    proc = run_helper(installed, *CONNECT_ARGS, stdin="wrong\n")

    assert proc.returncode == 1
    assert "Login failed." in (state / "mininfra.log").read_text()
    assert not (state / "mininfra.pid").exists()
    assert not (state / "mininfra.iface").exists()


def test_helper_connect_truncates_previous_log(helper):
    installed, state = helper
    run_helper(installed, *CONNECT_ARGS, stdin="wrong\n")
    run_helper(installed, "disconnect", "mininfra")

    run_helper(installed, *CONNECT_ARGS, stdin="good\n")

    assert (state / "mininfra.log").read_text().count("fake-openconnect:") == 1


def test_helper_disconnect_kills_process_and_removes_files(helper):
    installed, state = helper
    run_helper(installed, *CONNECT_ARGS, stdin="good\n")
    pid = int((state / "mininfra.pid").read_text())

    proc = run_helper(installed, "disconnect", "mininfra", "2")

    assert proc.returncode == 0, proc.stderr
    time.sleep(0.2)
    assert not status.pid_alive(pid)
    assert not (state / "mininfra.pid").exists()
    assert not (state / "mininfra.iface").exists()


def test_helper_disconnect_with_missing_or_stale_pid_is_a_noop(helper):
    installed, state = helper

    assert run_helper(installed, "disconnect", "mininfra").returncode == 0

    state.mkdir(exist_ok=True)
    (state / "mininfra.pid").write_text("999999\n")
    (state / "mininfra.iface").write_text("utun9 10.9.9.9\n")
    assert run_helper(installed, "disconnect", "mininfra").returncode == 0
    assert not (state / "mininfra.pid").exists()
    assert not (state / "mininfra.iface").exists()


def test_helper_disconnect_refuses_to_kill_a_process_that_is_not_openconnect(helper):
    installed, state = helper
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        state.mkdir(exist_ok=True)
        (state / "mininfra.pid").write_text(f"{sleeper.pid}\n")

        assert run_helper(installed, "disconnect", "mininfra").returncode == 0

        assert sleeper.poll() is None  # still alive: pid was treated as stale
        assert not (state / "mininfra.pid").exists()
    finally:
        sleeper.kill()


@pytest.mark.parametrize("bad_id", ["../etc", "Bad Id", "-x", "", "UPPER"])
def test_helper_rejects_invalid_ids(helper, bad_id):
    installed, _ = helper
    args = list(CONNECT_ARGS)
    args[1] = bad_id

    proc = run_helper(installed, *args, stdin="good\n")

    assert proc.returncode == 65
    assert "invalid id" in proc.stderr
    assert run_helper(installed, "disconnect", bad_id).returncode == 65


def test_helper_rejects_bad_grace_and_usage(helper):
    installed, _ = helper

    assert run_helper(installed, "disconnect", "mininfra", "soon").returncode == 65
    assert run_helper(installed).returncode == 64
    assert run_helper(installed, "bogus").returncode == 64
    assert run_helper(installed, "connect", "only", "three").returncode == 64
    assert run_helper(installed, "probe", "one").returncode == 64


def test_setup_script_replaces_every_placeholder_and_parses():
    setup = (SCRIPTS / "setup-privileges.sh").read_text()
    helper_src = (SCRIPTS / "vpnconnect-helper").read_text()
    split_src = (SCRIPTS / "vpnc-split.sh").read_text()

    for name in ("__STATE_DIR__", "__LIBEXEC_DIR__", "__OPENCONNECT__"):
        assert name in helper_src
        assert name in setup
    assert "__VPNC_SCRIPT__" in split_src
    assert "__VPNC_SCRIPT__" in setup
    assert "NOPASSWD" in setup
    assert "visudo -cf" in setup
    for script in ("setup-privileges.sh", "vpnconnect-helper", "vpnc-split.sh"):
        assert subprocess.run(["bash", "-n", str(SCRIPTS / script)]).returncode == 0
        assert os.access(SCRIPTS / script, os.X_OK)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scripts.py -q`
Expected: the new tests fail with `FileNotFoundError` for `scripts/vpnconnect-helper`.

- [ ] **Step 3: Write the helper**

`scripts/vpnconnect-helper`:

```bash
#!/bin/bash
# vpnconnect privileged helper.
#
# Installed root-owned by scripts/setup-privileges.sh and allowed through one
# sudoers.d line, so the Flask app (running as the normal user) can start and
# stop openconnect without a password prompt. Every file path is derived from
# STATE_DIR plus a validated id: the caller never chooses a path.
#
#   vpnconnect-helper probe <server> <authgroup> <protocol>
#   vpnconnect-helper connect <id> <server> <authgroup> <protocol> <username> <servercert|trusted-ca> <routes|->
#   vpnconnect-helper disconnect <id> [grace_seconds]
#
# connect reads the password from stdin and hands it to openconnect's stdin only.
# Exit codes: 64 usage, 65 invalid id or grace, otherwise openconnect's.
#
# Must run on macOS /bin/bash 3.2: no arrays, no mapfile.
set -euo pipefail

# Replaced by scripts/setup-privileges.sh at install time.
STATE_DIR="__STATE_DIR__"
LIBEXEC_DIR="__LIBEXEC_DIR__"
OPENCONNECT="__OPENCONNECT__"
VPNC_SPLIT="$LIBEXEC_DIR/vpnc-split.sh"

usage() {
  cat >&2 <<'USAGE'
usage: vpnconnect-helper probe <server> <authgroup> <protocol>
       vpnconnect-helper connect <id> <server> <authgroup> <protocol> <username> <servercert|trusted-ca> <routes|->
       vpnconnect-helper disconnect <id> [grace_seconds]
USAGE
  exit 64
}

require_id() {
  if ! [[ "$1" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    echo "vpnconnect-helper: invalid id '$1'" >&2
    exit 65
  fi
}

cmd_probe() {
  [ $# -eq 3 ] || usage
  local server="$1" authgroup="$2" protocol="$3" output
  # No password and --non-inter: openconnect stops right after the TLS handshake.
  # An untrusted certificate is reported with its pin; a trusted one continues
  # to a failed login. Either way the output is what the caller parses.
  output=$(echo "" | "$OPENCONNECT" "https://$server" --protocol="$protocol" \
    --authgroup="$authgroup" --user=probe --non-inter 2>&1 || true)
  printf '%s\n' "$output"
}

run_openconnect() {
  # $1 server, $2 authgroup, $3 protocol, $4 username, $5 pidfile, $6 logfile, rest: extra flags.
  local server="$1" authgroup="$2" protocol="$3" username="$4" pidfile="$5" logfile="$6"
  shift 6
  "$OPENCONNECT" "https://$server" --protocol="$protocol" --user="$username" \
    --authgroup="$authgroup" --passwd-on-stdin --non-inter --timestamp \
    --background --pid-file="$pidfile" --script="$VPNC_SPLIT" "$@" >>"$logfile" 2>&1
}

cmd_connect() {
  [ $# -eq 7 ] || usage
  local id="$1" server="$2" authgroup="$3" protocol="$4" username="$5" servercert="$6" routes="$7"
  require_id "$id"
  mkdir -p "$STATE_DIR"
  local pidfile="$STATE_DIR/$id.pid" logfile="$STATE_DIR/$id.log" ifacefile="$STATE_DIR/$id.iface"
  : >"$logfile"
  rm -f "$ifacefile"
  if [ "$routes" = "-" ]; then
    routes=""
  fi
  # openconnect passes its environment on to --script.
  export VPNCONNECT_ID="$id" VPNCONNECT_STATE_DIR="$STATE_DIR" VPNCONNECT_ROUTES="$routes"
  if [ "$servercert" = "trusted-ca" ]; then
    run_openconnect "$server" "$authgroup" "$protocol" "$username" "$pidfile" "$logfile"
  else
    run_openconnect "$server" "$authgroup" "$protocol" "$username" "$pidfile" "$logfile" \
      --servercert "$servercert"
  fi
}

cmd_disconnect() {
  { [ $# -ge 1 ] && [ $# -le 2 ]; } || usage
  local id="$1" grace="${2:-5}" pidfile ifacefile pid running waited=0
  require_id "$id"
  if ! [[ "$grace" =~ ^[0-9]+$ ]]; then
    echo "vpnconnect-helper: invalid grace '$grace'" >&2
    exit 65
  fi
  pidfile="$STATE_DIR/$id.pid"
  ifacefile="$STATE_DIR/$id.iface"
  if [ ! -f "$pidfile" ]; then
    rm -f "$ifacefile"
    return 0
  fi
  pid=$(tr -cd '0-9' <"$pidfile")
  # Only ever signal a process that really is openconnect; a stale pid may have
  # been reused by something else. ps -o comm shows argv[0] on macOS.
  running=$(ps -o comm= -o command= -p "${pid:-0}" 2>/dev/null || true)
  case "$running" in
    *openconnect*) ;;
    *)
      rm -f "$pidfile" "$ifacefile"
      return 0
      ;;
  esac
  kill -TERM "$pid" 2>/dev/null || true
  while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt "$grace" ]; do
    sleep 1
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile" "$ifacefile"
}

[ $# -ge 1 ] || usage
command="$1"
shift
case "$command" in
  probe) cmd_probe "$@" ;;
  connect) cmd_connect "$@" ;;
  disconnect) cmd_disconnect "$@" ;;
  *) usage ;;
esac
```

- [ ] **Step 4: Write the setup script**

`scripts/setup-privileges.sh`:

```bash
#!/bin/bash
# One-time privilege setup for vpnconnect. Run from the repository root:
#
#     sudo scripts/setup-privileges.sh
#
# Installs vpnconnect-helper and vpnc-split.sh root-owned under
# /usr/local/libexec/vpnconnect, bakes in the absolute paths they need, and
# allows the invoking user to run the helper (and only the helper) through
# sudo without a password. Safe to re-run after pulling changes to the scripts.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "run with sudo: sudo $0" >&2
  exit 1
fi
TARGET_USER="${SUDO_USER:-}"
if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = "root" ]; then
  echo "could not determine the invoking user (SUDO_USER is unset); run via sudo, not as root" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LIBEXEC_DIR="${LIBEXEC_DIR:-/usr/local/libexec/vpnconnect}"
STATE_DIR="${STATE_DIR:-$REPO_DIR/state}"
SUDOERS_FILE="/etc/sudoers.d/vpnconnect"
VPNC_SCRIPT="${VPNC_SCRIPT:-/opt/homebrew/etc/vpnc/vpnc-script}"
OPENCONNECT="${OPENCONNECT:-$(command -v openconnect || echo /opt/homebrew/bin/openconnect)}"

if [ ! -x "$OPENCONNECT" ]; then
  echo "openconnect not found at $OPENCONNECT; install it with: brew install openconnect" >&2
  exit 1
fi
if [ ! -f "$VPNC_SCRIPT" ]; then
  echo "vpnc-script not found at $VPNC_SCRIPT (set VPNC_SCRIPT=/path to override)" >&2
  exit 1
fi

mkdir -p "$LIBEXEC_DIR"
sed -e "s|__STATE_DIR__|$STATE_DIR|g" \
    -e "s|__LIBEXEC_DIR__|$LIBEXEC_DIR|g" \
    -e "s|__OPENCONNECT__|$OPENCONNECT|g" \
    "$REPO_DIR/scripts/vpnconnect-helper" >"$LIBEXEC_DIR/vpnconnect-helper"
sed -e "s|__VPNC_SCRIPT__|$VPNC_SCRIPT|g" \
    "$REPO_DIR/scripts/vpnc-split.sh" >"$LIBEXEC_DIR/vpnc-split.sh"
chown root:wheel "$LIBEXEC_DIR" "$LIBEXEC_DIR/vpnconnect-helper" "$LIBEXEC_DIR/vpnc-split.sh"
chmod 755 "$LIBEXEC_DIR" "$LIBEXEC_DIR/vpnconnect-helper" "$LIBEXEC_DIR/vpnc-split.sh"

mkdir -p "$STATE_DIR"
chown "$TARGET_USER" "$STATE_DIR"

TMP_SUDOERS="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: %s/vpnconnect-helper\n' "$TARGET_USER" "$LIBEXEC_DIR" >"$TMP_SUDOERS"
visudo -cf "$TMP_SUDOERS" >/dev/null
install -m 0440 -o root -g wheel "$TMP_SUDOERS" "$SUDOERS_FILE"
rm -f "$TMP_SUDOERS"

echo "installed:"
echo "  $LIBEXEC_DIR/vpnconnect-helper"
echo "  $LIBEXEC_DIR/vpnc-split.sh"
echo "  $SUDOERS_FILE  ($TARGET_USER may run the helper without a password)"
echo "state dir: $STATE_DIR"
echo
echo "check it works (should print 'ok' with no password prompt):"
echo "  sudo -n $LIBEXEC_DIR/vpnconnect-helper disconnect selftest && echo ok"
```

Then: `chmod +x scripts/vpnconnect-helper scripts/setup-privileges.sh`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scripts.py -q`
Expected: all pass. If `test_helper_disconnect_kills_process_and_removes_files` fails because the sleeper survives, check `ps -o comm=` output for the sleeper on this Mac; it must be `openconnect` (argv[0]).

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add scripts tests/test_scripts.py
git commit -m "feat: privileged helper and one-time sudo setup script

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Templates, README and final quality gate

**Files:**
- Create: `vpns.example.yaml`, `.env.example`, `state/.gitkeep`
- Modify: `README.md`, `.gitignore` (keep `state/.gitkeep`)

**Interfaces:**
- Consumes: everything above.
- Produces: a repository a new user can install from the README alone.

- [ ] **Step 1: Write the templates**

`vpns.example.yaml`:

```yaml
# Copy to vpns.yaml (git-ignored) and edit. No secrets here: usernames and
# passwords live in .env as VPN_<ID>_USERNAME / VPN_<ID>_PASSWORD, where <ID>
# is the id upper-cased with '-' replaced by '_'.
#
# The app writes `servercert` back into this file after the first connect, so
# hand-written comments in vpns.yaml disappear at that point. Keep notes here.
vpns:
  - id: mininfra                 # ^[a-z0-9][a-z0-9_-]*$  ->  VPN_MININFRA_USERNAME / _PASSWORD
    name: MININFRA office
    server: vpn.example.gov.rw   # host or host:port; https:// is added
    authgroup: Staff             # the "Group" dropdown in Cisco Secure Client
    routes:                      # networks forced into the tunnel if the server
      - 10.10.0.0/16             # pushes a full tunnel; leave out if the server
                                 # already sends split routes
  - id: rica-hq                  # ->  VPN_RICA_HQ_USERNAME / _PASSWORD
    name: RICA headquarters
    server: vpn.example.rw
    authgroup: Employees
    routes:
      - 172.16.0.0/12
```

`.env.example`:

```
SECRET_KEY=change-me

# One username/password pair per VPN id in vpns.yaml.
# The id is upper-cased and '-' becomes '_': rica-hq -> VPN_RICA_HQ_...
VPN_MININFRA_USERNAME=
VPN_MININFRA_PASSWORD=
VPN_RICA_HQ_USERNAME=
VPN_RICA_HQ_PASSWORD=

# Optional overrides (defaults shown).
# VPNCONNECT_VPNS_FILE=./vpns.yaml
# VPNCONNECT_STATE_DIR=./state
# VPNCONNECT_HELPER=/usr/local/libexec/vpnconnect/vpnconnect-helper
# VPNCONNECT_CONNECT_TIMEOUT=30
# VPNCONNECT_DISCONNECT_GRACE=5
```

`state/.gitkeep`: empty file. Change the `state/` line in `.gitignore` to:

```
state/*
!state/.gitkeep
```

- [ ] **Step 2: Write the README**

`README.md`:

````markdown
# vpnconnect

Run several Cisco AnyConnect VPNs at the same time from one local dashboard.

Cisco Secure Client allows one session at a time. `openconnect` does not: every
connection is its own process on its own `utun` interface. vpnconnect wraps
that in a small Flask app so you can press **Connect all** and reach every
internal network you need, with normal internet traffic staying on Wi-Fi.

## How it works

- `vpns.yaml` lists your VPNs (server, group, routes). `.env` holds the
  passwords. Both files are git-ignored.
- The dashboard at `http://127.0.0.1:5000` and the REST API under `/api/v1`
  start and stop tunnels.
- Tunnels are started by a root-owned helper (`/usr/local/libexec/vpnconnect/
  vpnconnect-helper`) that a single `sudoers.d` line lets your user run without
  a password. It runs `openconnect` with the password on stdin, in the
  background, with a pid file and a log file per VPN.
- A wrapper around `vpnc-script` guarantees **split routing**: if a server
  pushes a full tunnel, only the `routes` you configured for that VPN go
  through it. If a server pushes a full tunnel and you configured no routes,
  the connection is refused instead of hijacking your default route.
- The first connect to a server probes its certificate pin and stores it in
  `vpns.yaml` as `servercert`. Later connects verify against it.

## Requirements

- macOS with Homebrew, `openconnect` and its `vpnc-script`:
  `brew install openconnect`
- Python 3.13 and [uv](https://docs.astral.sh/uv/)

## Install

```bash
git clone https://github.com/DUSENGEYEZU/vpnconnect.git
cd vpnconnect
uv sync
cp vpns.example.yaml vpns.yaml
cp .env.example .env
```

Edit `vpns.yaml` with your servers and `.env` with your usernames and
passwords. Variable names follow the VPN id: `id: rica-hq` reads
`VPN_RICA_HQ_USERNAME` and `VPN_RICA_HQ_PASSWORD`.

### One-time privilege setup

```bash
sudo scripts/setup-privileges.sh
```

This copies the helper and the split-routing wrapper to
`/usr/local/libexec/vpnconnect/` (root-owned), creates `state/`, and writes
`/etc/sudoers.d/vpnconnect` with exactly one line allowing your user to run the
helper. Re-run it after updating the scripts. Check it:

```bash
sudo -n /usr/local/libexec/vpnconnect/vpnconnect-helper disconnect selftest && echo ok
```

To undo: `sudo rm -r /usr/local/libexec/vpnconnect /etc/sudoers.d/vpnconnect`.

## Run

```bash
uv run flask run
```

Open <http://127.0.0.1:5000>. Swagger UI is at <http://127.0.0.1:5000/docs/>.
While developing, `uv run flask run --debug` enables the reloader.

On macOS, port 5000 may be taken by AirPlay Receiver. Change
`FLASK_RUN_PORT` in `.flaskenv` or run `uv run flask run --port 5050`.

## Configuration

### `vpns.yaml`

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | `^[a-z0-9][a-z0-9_-]*$`; drives the env var names |
| `name` | no | Display name (defaults to the id) |
| `server` | yes | Host or `host:port`; `https://` is added |
| `authgroup` | yes | The group shown in Cisco Secure Client's dropdown |
| `protocol` | no | openconnect protocol, default `anyconnect` |
| `routes` | no | CIDRs forced into the tunnel when the server pushes a full tunnel |
| `servercert` | no | Written by the app after the first probe; delete it to re-probe |

`vpns.yaml` is rewritten when a pin is stored, and comments do not survive
that rewrite. Keep notes in `vpns.example.yaml`.

### `.env`

`VPN_<ID>_USERNAME`, `VPN_<ID>_PASSWORD` per VPN, plus optional
`VPNCONNECT_VPNS_FILE`, `VPNCONNECT_STATE_DIR`, `VPNCONNECT_HELPER`,
`VPNCONNECT_CONNECT_TIMEOUT` (default 30 s), `VPNCONNECT_DISCONNECT_GRACE`
(default 5 s). Host and port come from `.flaskenv`.

## API

All responses are JSON. Errors look like `{"error": "..."}`.

| Method | Path | Result |
|---|---|---|
| GET | `/api/v1/health` | `{"status": "ok"}` |
| GET | `/api/v1/vpns` | every VPN with `state`, `interface`, `ip`, `routes_count`, `message` |
| GET | `/api/v1/vpns/{id}` | one VPN (404 if unknown) |
| POST | `/api/v1/vpns/{id}/connect` | 202 and connects in the background; 400 missing credentials; 409 already up |
| POST | `/api/v1/vpns/{id}/disconnect` | 202; 409 if not connected |
| POST | `/api/v1/vpns/connect-all` | 202 `{"started": [...], "skipped": [{"id", "reason"}]}` |
| POST | `/api/v1/vpns/disconnect-all` | 202, same shape |
| GET | `/api/v1/vpns/{id}/log?lines=50` | last log lines from openconnect |

States: `disconnected`, `connecting`, `connected`, `disconnecting`, `error`.

```bash
curl -X POST http://127.0.0.1:5000/api/v1/vpns/connect-all
curl http://127.0.0.1:5000/api/v1/vpns | python3 -m json.tool
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `privilege helper not available: run sudo scripts/setup-privileges.sh` | The sudoers line or the helper is missing. Run the setup script. |
| `missing credentials: VPN_X_PASSWORD` | Add the variable to `.env` and restart `flask run`. |
| `Login failed.` | Wrong username, password or authgroup. |
| `Certificate from VPN server ... failed verification` | The server's certificate changed. If that is expected, delete `servercert` for that VPN in `vpns.yaml` and connect again. |
| `vpnconnect: server pushed a full tunnel and no routes are configured` | Add a `routes:` list for that VPN. |
| `timed out after 30 s` | Check the row's log; the server may be unreachable or require a second factor (not supported). |
| `tunnel dropped: ...` | openconnect exited on its own (network change, server timeout). Connect again. |
| Row shows `adopted running tunnel` | The app restarted while the tunnel stayed up. Everything is fine. |
| Two VPNs need the same internal network | The first route added wins. Check `netstat -rn -f inet`. |

Cisco Secure Client can stay installed. Its own session is not shown or
controlled here.

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Tests never touch sudo, openconnect or the network: the helper and the
wrapper are exercised against a fake `openconnect` and a stub `vpnc-script`.

Design: `docs/superpowers/specs/2026-09-17-vpnconnect-design.md`.
Plan: `docs/superpowers/plans/2026-09-17-vpnconnect.md`.
````

- [ ] **Step 3: Run the full quality gate**

Run:

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
```

Expected: no lint or format errors; every test passes.

- [ ] **Step 4: Smoke-test app start against the example file**

Run:

```bash
VPNCONNECT_VPNS_FILE=vpns.example.yaml VPNCONNECT_STATE_DIR=/tmp/vpnconnect-smoke \
  uv run python -c "from app import create_app; c = create_app().test_client(); print(c.get('/api/v1/vpns').get_json()['vpns'][0]['state'], c.get('/').status_code)"
```

Expected: `disconnected 200`.

- [ ] **Step 5: Commit**

```bash
git add README.md vpns.example.yaml .env.example .gitignore state/.gitkeep
git commit -m "docs: README, example config and env templates

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Manual acceptance with Longin (no code)

This task is performed with Longin present. Claude never runs step 1.

- [ ] **Step 1 (Longin):** `sudo scripts/setup-privileges.sh`, then the self-test line it prints.
- [ ] **Step 2 (Longin):** Fill `vpns.yaml` and `.env` for two real VPNs. Quit Cisco Secure Client's session if it is connected to one of them.
- [ ] **Step 3:** `uv run flask run`, open <http://127.0.0.1:5000>, press **Connect all**.
- [ ] **Step 4:** Both rows turn green with different `utun` interfaces. Verify:

```bash
netstat -rn -f inet | grep -E 'default|utun'
```

The `default` route must still point at `en0` (or the Wi-Fi interface); each `utun` shows only internal networks.

- [ ] **Step 5:** Reach one internal host per VPN (a browser URL or `ping`).
- [ ] **Step 6:** Press **Disconnect all**. Interfaces and routes disappear; `ls state/` shows no `.pid` or `.iface` files.
- [ ] **Step 7:** Record anything unexpected as follow-up issues; if the Cisco socket-filter extension interferes, note the workaround (quit Cisco Secure Client) in the README's Troubleshooting table.
