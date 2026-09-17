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
    HELPER = os.environ.get("VPNCONNECT_HELPER", "/usr/local/libexec/vpnconnect/vpnconnect-helper")
    # Seconds to wait for the tunnel interface after openconnect starts.
    CONNECT_TIMEOUT = env_int("VPNCONNECT_CONNECT_TIMEOUT", 30)
    # Seconds between SIGTERM and SIGKILL when disconnecting.
    DISCONNECT_GRACE = env_int("VPNCONNECT_DISCONNECT_GRACE", 5)
