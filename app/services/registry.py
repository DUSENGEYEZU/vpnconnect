"""VPN definitions from vpns.yaml plus credentials from the environment.

vpns.yaml holds no secrets and may be edited by hand. Credentials are read
from VPN_<ID>_USERNAME / VPN_<ID>_PASSWORD, where <ID> is the id upper-cased
with '-' replaced by '_'.
"""

from __future__ import annotations

import ipaddress
import os
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


# Keys of a vpns.yaml entry the dashboard and the API may write. Everything
# else in an entry (protocol, servercert) is kept as it is found.
FORM_FIELDS = ("name", "server", "authgroup", "routes")


class RegistryError(ValueError):
    """vpns.yaml is missing, malformed, or contains an invalid entry."""


class DuplicateVpn(RegistryError):
    """An entry with that id is already in vpns.yaml."""


@dataclass(frozen=True)
class VpnDef:
    id: str
    name: str
    server: str
    authgroup: str
    protocol: str = "anyconnect"
    routes: tuple[ipaddress.IPv4Network, ...] = ()
    servercert: str | None = None
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)

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
            network = ipaddress.IPv4Network(str(raw), strict=False)
        except ValueError as exc:
            raise RegistryError(f"vpns[{vpn_id}]: route {raw!r} is not an IPv4 network") from exc
        if network.prefixlen == 0:
            raise RegistryError(
                f"vpns[{vpn_id}]: route {raw!r} is a default route; "
                "default routes are rejected because they would defeat split routing"
            )
        routes.append(network)

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
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path}: invalid YAML: {exc}") from exc
    entries = data.get("vpns") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise RegistryError(f"{path}: top-level 'vpns' must be a list")

    vpns = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RegistryError(f"vpns[#{index}]: entry must be a mapping")
        vpns.append(parse_vpn(entry, index, env))
    seen: set[str] = set()
    for vpn in vpns:
        if vpn.id in seen:
            raise RegistryError(f"vpns[{vpn.id}]: duplicate id")
        seen.add(vpn.id)
    return Registry(path=path, vpns=vpns)


def _read_file(path: Path) -> tuple[dict[str, Any], list[Any]]:
    """The parsed file and its 'vpns' list, both still mutable for a rewrite."""
    if not path.exists():
        raise RegistryError(f"{path} does not exist")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path}: invalid YAML: {exc}") from exc
    entries = data.get("vpns") if isinstance(data, dict) else None
    if entries is None and isinstance(data, dict) and "vpns" not in data:
        entries = data["vpns"] = []
    if not isinstance(data, dict) or not isinstance(entries, list):
        raise RegistryError(f"{path}: top-level 'vpns' must be a list")
    return data, entries


def _find(entries: list[Any], vpn_id: str) -> tuple[int, dict[str, Any]]:
    for index, entry in enumerate(entries):
        if isinstance(entry, dict) and entry.get("id") == vpn_id:
            return index, entry
    raise RegistryError(f"vpns[{vpn_id}]: not found")


def _write_file(path: Path, data: Any) -> None:
    """Rewrite vpns.yaml atomically: temp file in the same directory, then
    os.replace, so a concurrent reader never sees a half-written file."""
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def save_servercert(path: str | Path, vpn_id: str, value: str) -> None:
    """Write servercert for one VPN back into vpns.yaml, keeping every other key.

    PyYAML does not keep comments, so a hand-written comment in vpns.yaml is
    lost the first time the app stores a pin. The README says so.
    """
    path = Path(path)
    data, entries = _read_file(path)
    _find(entries, vpn_id)[1]["servercert"] = value
    _write_file(path, data)


def add_vpn(path: str | Path, fields: Mapping[str, Any]) -> VpnDef:
    """Validate fields and append one entry to vpns.yaml.

    Returns the parsed definition, without credentials: those live in the
    environment, so the caller reloads the registry to see them.
    """
    path = Path(path)
    data, entries = _read_file(path)
    vpn = parse_vpn(fields, len(entries), env={})
    if any(isinstance(entry, dict) and entry.get("id") == vpn.id for entry in entries):
        raise DuplicateVpn(f"vpns[{vpn.id}]: duplicate id")
    entries.append(_entry(vpn))
    _write_file(path, data)
    return vpn


def update_vpn(path: str | Path, vpn_id: str, fields: Mapping[str, Any]) -> VpnDef:
    """Apply the FORM_FIELDS present in fields to one entry. The id never
    changes; a new server drops the stored servercert so the next connect
    probes the new host.
    """
    path = Path(path)
    data, entries = _read_file(path)
    index, entry = _find(entries, vpn_id)
    merged = {key: value for key, value in fields.items() if key in FORM_FIELDS}
    vpn = parse_vpn({**entry, **merged, "id": vpn_id}, index, env={})
    updated = {**entry, **_entry(vpn)}
    if not vpn.routes:
        updated.pop("routes", None)
    if vpn.server != entry.get("server"):
        updated.pop("servercert", None)
    entries[index] = updated
    _write_file(path, data)
    return vpn


def remove_vpn(path: str | Path, vpn_id: str) -> None:
    """Delete one entry from vpns.yaml."""
    path = Path(path)
    data, entries = _read_file(path)
    del entries[_find(entries, vpn_id)[0]]
    _write_file(path, data)


def _entry(vpn: VpnDef) -> dict[str, Any]:
    """The keys this module writes for one VPN, in a stable order."""
    entry: dict[str, Any] = {
        "id": vpn.id,
        "name": vpn.name,
        "server": vpn.server,
        "authgroup": vpn.authgroup,
    }
    if vpn.routes:
        entry["routes"] = [str(net) for net in vpn.routes]
    return entry
