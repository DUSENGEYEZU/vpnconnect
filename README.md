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
