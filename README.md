---
noteId: "8b968bc0b35411f1975947b2236733b6"
tags: []

---

# vpnconnect

Run several Cisco AnyConnect VPNs at the same time from one local dashboard.

Cisco Secure Client allows one session at a time. `openconnect` does not: every
connection is its own process on its own `utun` interface. vpnconnect wraps
that in a small Flask app so you can press **Connect all** and reach every
internal network you need, with normal internet traffic staying on Wi-Fi.

## How it works

- `vpns.yaml` lists your VPNs (server, group, routes). `.env` holds the
  passwords. Both files are git-ignored, and the dashboard can edit both.
- The dashboard at `http://127.0.0.1:5110` and the REST API under `/api/v1`
  start and stop tunnels.
- Tunnels are started by a root-owned helper (`/usr/local/libexec/vpnconnect/
  vpnconnect-helper`) that a single `sudoers.d` line lets your user run without
  a password. It runs `openconnect` with the password on stdin, in the
  background, with a pid file and a log file per VPN.
- A wrapper around `vpnc-script` guarantees **IPv4 split routing**: if a server
  pushes a full tunnel — including a split list that contains a default route —
  only the `routes` you configured for that VPN go through it. If you
  configured no routes, the wrapper installs none and the app aborts the
  connect as soon as the refusal reaches the log, instead of hijacking your
  default route. IPv6 is disabled on every tunnel (`--disable-ipv6`), so a
  server cannot take the IPv6 default route either.
- The wrapper also owns **DNS**. It adds a host route for every DNS server a
  VPN pushes, as Cisco's client does, and registers those servers only for
  that VPN's own domain (a *supplemental* resolver), and only if one of them
  answers through the tunnel. Your normal DNS is never touched, so an
  unreachable VPN resolver cannot stall the rest of your Mac. If a server
  pushes DNS without a domain, or its DNS does not answer, the log says so and
  you reach that VPN's hosts by IP.
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

Open <http://127.0.0.1:5110>. Swagger UI is at <http://127.0.0.1:5110/docs/>.
While developing, `uv run flask run --debug` enables the reloader.

The default port is 5110 because macOS AirPlay Receiver often occupies 5000. Change
`FLASK_RUN_PORT` in `.flaskenv` or run `uv run flask run --port 5050`.

### In the background

Start the dashboard detached from the terminal (it keeps running when you close
the window) and stop it again:

```bash
scripts/app-start.sh   # runs `uv run flask run` in the background, log in ~/Library/Logs/vpnconnect.log
scripts/app-stop.sh    # stops it
```

`app-start.sh` prints the URL once the app answers; running it twice is safe.
Stopping the dashboard does not close the tunnels: openconnect runs as root on
its own, and the dashboard adopts the running tunnels when it starts again.
Press **Disconnect all** first if you want the tunnels down too. The app does
not start automatically at login: macOS blocks background services from
`~/Documents`, where this project lives, so run `scripts/app-start.sh` after
a reboot.

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
`VPNCONNECT_VPNS_FILE`, `VPNCONNECT_ENV_FILE`, `VPNCONNECT_STATE_DIR`,
`VPNCONNECT_HELPER`, `VPNCONNECT_CONNECT_TIMEOUT` (default 30 s),
`VPNCONNECT_DISCONNECT_GRACE` (default 5 s). Host and port come from
`.flaskenv`.

`VPNCONNECT_STATE_DIR` is not a free override: the installed helper derives
every pid, log and iface path from the `STATE_DIR` baked into it, so the two
must match. After changing it, re-run the setup script with the same value:

```bash
sudo STATE_DIR=/absolute/path/to/state scripts/setup-privileges.sh
```

The app logs a warning at startup when the two differ.

### Managing VPNs from the dashboard

**Add VPN** in the header and **Edit** / **Delete** on a row write the same two
files, so they stay the source of truth and hand editing keeps working.

- Per VPN: `id`, `name`, `server`, `authgroup` and `routes` go to `vpns.yaml`,
  the username and password to `.env`. Nothing else is editable from the UI.
- The `id` names the env variables and the state files, so it cannot change:
  delete the VPN and add it again instead.
- Passwords are write-only. An empty password field keeps the stored one, and
  no response, log line or page ever shows one.
- Changing `server` drops the stored `servercert`, so the next connect probes
  the new host's certificate.
- Edit and Delete are refused while the tunnel is connecting, connected or
  disconnecting. Delete also removes that VPN's two lines from `.env` and its
  `state/<id>.log`. A VPN in `error` whose `openconnect` process is still
  running cannot be deleted either: press **Disconnect** first, otherwise the
  entry would go and leave that process behind.
- Changes apply at once: the app re-reads `vpns.yaml` and the credentials
  after every change, and tunnels that stay up keep running.
- App settings (`SECRET_KEY`, `VPNCONNECT_*`) are **not** editable from the
  UI. They are read once at startup, and `VPNCONNECT_STATE_DIR` has to match
  the installed helper, so edit `.env` by hand and restart `flask run`.
- The API has no authentication, so every `POST`, `PUT` and `DELETE` is
  refused unless the request is for `127.0.0.1`/`localhost` and, when a
  browser sends an `Origin` header, that origin is the dashboard's own.

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
| POST | `/api/v1/vpns` | 201 adds a VPN with its credentials; 400 invalid field; 409 duplicate id |
| PUT | `/api/v1/vpns/{id}` | 200 changes it; 400 invalid; 404 unknown; 409 not disconnected |
| DELETE | `/api/v1/vpns/{id}` | 204 removes it with its credentials and log; 404; 409 not disconnected |

States: `disconnected`, `connecting`, `connected`, `disconnecting`, `error`.

```bash
curl -X POST http://127.0.0.1:5110/api/v1/vpns/connect-all
curl http://127.0.0.1:5110/api/v1/vpns | python3 -m json.tool
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `privilege helper not available: run sudo scripts/setup-privileges.sh` | The sudoers line or the helper is missing. Run the setup script. |
| `missing credentials: VPN_X_PASSWORD` | Set the password with **Edit** on that row, or add the variable to `.env` by hand and restart `flask run`. |
| `Login failed.` | Wrong username, password or authgroup. |
| `Certificate from VPN server ... failed verification` | The server's certificate changed. If that is expected, delete `servercert` for that VPN in `vpns.yaml` and connect again. |
| `vpnconnect: server pushed a full tunnel and no routes are configured` | Add a `routes:` list for that VPN. |
| `timed out after 30 s` | Check the row's log; the server may be unreachable or require a second factor (not supported). |
| `tunnel dropped: ...` | openconnect exited on its own (network change, server timeout). Connect again. |
| Row shows `adopted running tunnel` | The app restarted while the tunnel stayed up. Everything is fine. |
| Row stays `connected` but nothing is reachable | The pid in `state/<id>.pid` was reused by another root process, so the app still sees a live pid. Press Disconnect: the helper only signals a process actually named `openconnect` and otherwise just clears the files. Then connect again. |
| Two VPNs need the same internal network | The first route added wins. Check `netstat -rn -f inet`. |

Cisco Secure Client can stay installed. Its own session is not shown or
controlled here.

## Security notes

- The `sudoers.d` line grants passwordless root to one root-owned script. That
  script runs `/opt/homebrew/bin/openconnect` and
  `/opt/homebrew/etc/vpnc/vpnc-script`, and Homebrew leaves both writable by
  your own user: anything running as you can edit them and be run as root
  without a password. On a single-user machine that may be an acceptable
  trade; to close it, install root-owned copies and bake those paths in:

  ```bash
  sudo OPENCONNECT=/usr/local/sbin/openconnect \
       VPNC_SCRIPT=/usr/local/sbin/vpnc-script \
       scripts/setup-privileges.sh
  ```

- Passwords are read from `.env` and reach `openconnect` on stdin only. They
  are never a command-line argument, a log line or part of an API response.
  The dashboard can write them to `.env` (mode 0600) but never reads one back.
- Mutating requests are refused unless they come from the dashboard's own
  host and origin, so a page on another site cannot drive your tunnels.
- The server binds to 127.0.0.1 and has no authentication: anyone able to run
  code as your user can start and stop your tunnels.

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
