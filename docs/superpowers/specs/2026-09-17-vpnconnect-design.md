# vpnconnect: run several Cisco AnyConnect VPNs at once from one dashboard

Date: 2026-09-17
Status: approved design, ready for implementation planning

## 1. Problem

Longin connects to several Cisco AnyConnect VPNs every day. Cisco Secure Client
allows one session at a time, so each server means disconnecting from the
previous one. The KUBAKA deploy workflow (`.github/workflows/api-prod.yml`,
"Connect to VPN" step) already shows a scriptable alternative: `openconnect`
driven by server, authgroup, username, password and a certificate pin.

`openconnect` has no single-session limit. Each connection is its own process
on its own `utun` interface with its own routes and DNS entry. Several can run
together as long as none of them takes over the default route.

## 2. Goal

A local Flask app (same stack as `social_media_project`) that:

- lists the VPNs defined in a config file,
- connects to one, or all of them at once, using `openconnect`,
- enforces split routing so tunnels never fight over the default route,
- shows live status (interface, tunnel IP, route count) on a small web page,
- exposes the same actions as a REST API documented with Swagger.

## 3. Non-goals (v1)

- Controlling or reading the Cisco Secure Client GUI session.
- The macOS IPSec profile ("VPN") and the sing-box profile ("SFM").
- OpenVPN, WireGuard, FortiClient, Pulse, GlobalProtect (openconnect could do
  the last three later through `protocol`, but v1 is anyconnect only).
- Auto-reconnect, menu-bar app, Keychain storage, multi-user access.

## 4. Decisions taken

| Question | Decision |
|---|---|
| VPN types | Cisco AnyConnect only, via `openconnect --protocol=anyconnect` |
| Routing | Split routing always; a tunnel may never own the default route |
| App form | Flask web dashboard plus REST API, Python 3.13, uv |
| Credentials | `.env` file, git-ignored, same naming style as the GitHub secrets |
| Privileges | One root-owned helper script allowed through one `sudoers.d` line |
| Certificate pin | Probed once, stored in `vpns.yaml`, verified on later connects |

## 5. Stack

Copied from `social_media_project/social_media`:

- Python >= 3.13, managed with `uv` (`pyproject.toml`, `uv.lock`, `.python-version`).
- Flask 3 app factory, blueprint mounted at `/api/v1`, flasgger Swagger UI at `/docs/`
  and the raw spec at `/openapi.json` (same layout as social_media).
- `python-dotenv` for `.env`, `PyYAML` for `vpns.yaml`.
- Dev: `pytest`, `ruff`. `[tool.pytest.ini_options] testpaths = ["tests"]`.
- Runtime dependency on the host: `openconnect` (Homebrew, v9.21 present) and
  its `vpnc-script` at `/opt/homebrew/etc/vpnc/vpnc-script`.

## 6. Repository layout

```
vpnconnect/
  pyproject.toml
  .python-version
  .env.example                 documents VPN_<ID>_USERNAME / VPN_<ID>_PASSWORD
  .flaskenv                    FLASK_APP, FLASK_RUN_HOST=127.0.0.1, FLASK_RUN_PORT=5000
  .gitignore                   .env, vpns.yaml, .venv/, state/, caches
  vpns.example.yaml            committed template; copied to vpns.yaml
  vpns.yaml                    real VPN definitions, git-ignored (hostnames stay off GitHub);
                               the app writes servercert back here
  README.md
  app/
    __init__.py                create_app(config_class, manager=None) -> Flask
    config.py                  Config: paths, timeouts, host/port
    docs.py                    flasgger init (as in social_media)
    api/
      __init__.py              api_bp = Blueprint("api", ...)
      health.py                GET /health
      vpns.py                  VPN endpoints (section 9)
    services/
      registry.py              load vpns.yaml, merge credentials from env, save servercert
      runner.py                thin subprocess boundary: HelperRunner protocol, SudoHelperRunner
      tunnel.py                TunnelManager: state machine, connect/disconnect threads
      status.py                read pid/iface files, parse failures from log, count routes per interface
    templates/
      index.html               dashboard (vanilla JS, polls /api/v1/vpns every 3 s)
  scripts/
    vpnconnect-helper          bash 3.2, installed root-owned; probe / connect / disconnect
    vpnc-split.sh              bash 3.2, installed root-owned; forces split routes, runs vpnc-script,
                               records <id>.iface
    setup-privileges.sh        run once with sudo; installs the two files and the sudoers line
  state/                       <id>.pid, <id>.log, <id>.iface (git-ignored)
  docs/superpowers/specs/      this file
  tests/
```

## 7. Configuration

### 7.1 `vpns.yaml`

```yaml
vpns:
  - id: mininfra              # ^[a-z0-9][a-z0-9_-]*$ ; used to build env var names
    name: MININFRA office     # display name
    server: vpn.example.rw    # host or host:port; the app prefixes https://
    authgroup: Employees      # openconnect --authgroup
    protocol: anyconnect      # optional, default anyconnect
    routes:                   # optional list of CIDRs forced into the tunnel when the
      - 10.10.0.0/16          # server pushes a full tunnel (section 8.3)
    servercert: pin-sha256:…  # optional; written by the app after the first probe.
                              # The literal value "trusted-ca" means the server cert
                              # validated against the system store and no pin is used.
```

Validation on load: unique ids, id pattern, `server` and `authgroup` present,
each route parses as an IPv4 network. A bad file fails app start with the
offending entry named.

### 7.2 `.env`

For each VPN id, uppercased with `-` replaced by `_`:

```
VPN_MININFRA_USERNAME=longin
VPN_MININFRA_PASSWORD=…
```

Optional app settings with defaults (host and port live in `.flaskenv` as
`FLASK_RUN_HOST=127.0.0.1` / `FLASK_RUN_PORT=5000`):

```
VPNCONNECT_VPNS_FILE=./vpns.yaml
VPNCONNECT_STATE_DIR=./state
VPNCONNECT_HELPER=/usr/local/libexec/vpnconnect/vpnconnect-helper
VPNCONNECT_CONNECT_TIMEOUT=30      # seconds to wait for the <id>.iface file to appear
VPNCONNECT_DISCONNECT_GRACE=5      # seconds between SIGTERM and SIGKILL
```

The server binds to 127.0.0.1 only. There is no authentication on the API; it
is a single-user local tool.

## 8. Privileged layer

### 8.1 Why a helper

`openconnect` must run as root to create a `utun` device, and stopping it means
signalling a root process. Instead of `NOPASSWD` on `openconnect` and `kill`
(which would allow killing any root process), the app calls one root-owned
script with a fixed, narrow interface. `sudoers.d/vpnconnect` contains exactly:

```
<user> ALL=(root) NOPASSWD: /usr/local/libexec/vpnconnect/vpnconnect-helper
```

`setup-privileges.sh` (run once: `sudo scripts/setup-privileges.sh`) copies
`vpnconnect-helper` and `vpnc-split.sh` to `/usr/local/libexec/vpnconnect/`
(root:wheel, 0755), bakes the absolute state directory and vpnc-script path into
the helper, writes the sudoers line for `$SUDO_USER`, validates it with
`visudo -cf`, and creates the state directory. It is idempotent.

### 8.2 `vpnconnect-helper` interface

All ids are validated against `^[a-z0-9][a-z0-9_-]*$`; the helper derives every
path from the baked state directory plus the id, so callers cannot choose file
paths.

```
vpnconnect-helper probe <server> <authgroup> <protocol>
    Runs: echo "" | openconnect https://<server> --protocol=<protocol> \
              --authgroup=<authgroup> --user=probe --non-inter 2>&1
    Prints the first pin-sha256:… found (same grep as api-prod.yml) or nothing.
    Exit 0 either way; the caller decides.

vpnconnect-helper connect <id> <server> <authgroup> <protocol> <username> \
                          <servercert|trusted-ca> <routes|->
    Reads the password from stdin.
    Truncates <state>/<id>.log, removes any stale <state>/<id>.iface, exports
    VPNCONNECT_ID=<id>, VPNCONNECT_STATE_DIR=<state> and VPNCONNECT_ROUTES=<routes>
    (openconnect passes its environment to the script), then runs:
      openconnect https://<server> --protocol=<protocol> --user=<username> \
        --authgroup=<authgroup> --passwd-on-stdin --non-inter --timestamp \
        [--servercert <servercert>] --background --pid-file=<state>/<id>.pid \
        --script=/usr/local/libexec/vpnconnect/vpnc-split.sh \
        >> <state>/<id>.log 2>&1
    Returns openconnect's exit code. With --background, openconnect returns
    once the tunnel is up or authentication has failed. openconnect 9.21 logs
    "Configured as <ip>, with SSL connected …" on success; that line does not
    name the interface, which is why the wrapper records it (section 8.3).

vpnconnect-helper disconnect <id> [grace_seconds]
    Reads <state>/<id>.pid. Verifies `ps -o comm= -p <pid>` ends with
    "openconnect"; otherwise removes the stale pid file and exits 0.
    kill -TERM, waits up to grace_seconds (default 5), kill -KILL if still alive,
    removes the pid file and <state>/<id>.iface. Exit 0.
```

`<routes>` is a comma-separated list of `addr:mask:len` triples precomputed by
Python (for example `10.10.0.0:255.255.0.0:16`), or `-` for none.

### 8.3 `vpnc-split.sh`: the split-routing guarantee

`openconnect` calls the script with the connection described in environment
variables (`reason`, `TUNDEV`, `CISCO_SPLIT_INC*`, …). The wrapper:

1. If `CISCO_SPLIT_INC` is set and >= 1, the server sent split routes. Leave
   the environment alone.
2. Else if `VPNCONNECT_ROUTES` is non-empty, export `CISCO_SPLIT_INC=<n>` and
   `CISCO_SPLIT_INC_<i>_ADDR/_MASK/_MASKLEN` for each triple.
3. Else print `vpnconnect: server pushed a full tunnel and no routes are
   configured for this VPN; refusing to take the default route` to stderr and
   exit 1. `openconnect` treats a failed connect script as fatal, so the
   connection aborts and the message lands in the log.
4. Run `/opt/homebrew/etc/vpnc/vpnc-script "$@"` and keep its exit code.
5. Record the interface: on `reason=connect` or `reconnect`, and only if
   vpnc-script succeeded, write `"$TUNDEV $INTERNAL_IP4_ADDRESS"` to
   `$VPNCONNECT_STATE_DIR/$VPNCONNECT_ID.iface`; on `reason=disconnect`, remove
   that file. Exit with vpnc-script's code. The app treats the presence of this
   file plus a live pid as "connected". `TUNDEV` and `INTERNAL_IP4_ADDRESS` are
   supplied by openconnect to every script invocation.

Steps 1 to 3 run for `reason=connect` and `reconnect`; on `disconnect` the
routes are re-injected (never refused) so vpnc-script removes exactly what it
added; `pre-init` and `attempt-reconnect` pass straight through, since no route
information exists yet at that point. The stock
vpnc-script only sets a default route when `CISCO_SPLIT_INC` is unset, which is
what makes step 2 sufficient. DNS: vpnc-script registers the tunnel's DNS
servers under `State:/Network/Service/<TUNDEV>/DNS`, one entry per tunnel, so
several tunnels coexist.

## 9. Application layer

### 9.1 `registry.py`

- `load_registry(path, env) -> Registry` with `Registry.vpns: list[VpnDef]`.
- `VpnDef`: id, name, server, authgroup, protocol, routes (list of
  `ipaddress.IPv4Network`), servercert (str | None), username (str | None),
  password (str | None), `has_credentials` property, `routes_arg()` producing
  the `addr:mask:len,…` string or `-`.
- `save_servercert(path, vpn_id, value)` rewrites only that key in the YAML,
  preserving the rest of the file.

### 9.2 `runner.py`

A single seam for everything that touches the system:

```python
class HelperRunner(Protocol):
    def probe(self, vpn: VpnDef) -> str            # raw combined output
    def connect(self, vpn: VpnDef) -> int          # exit code; password via stdin
    def disconnect(self, vpn_id: str) -> int
class SudoHelperRunner: builds ["sudo", "-n", helper_path, ...] commands
```

Tests use a fake runner; nothing in `tests/` spawns processes except the
wrapper test, which runs `vpnc-split.sh` against a stub `vpnc-script` that
dumps its environment.

### 9.3 `status.py`

Pure functions over the state directory:

- `read_pid(state_dir, id) -> int | None`.
- `pid_alive(pid) -> bool`: `os.kill(pid, 0)`; `PermissionError` counts as
  alive (root process), `ProcessLookupError` as dead.
- `read_iface(state_dir, id) -> (interface, ip) | None`: parses `<id>.iface`
  written by the wrapper (section 8.3).
- `parse_failure(log_text) -> str | None`: last line matching known failures
  (`Login failed`, `Certificate .* failed verification`, `Failed to`, the
  wrapper's `vpnconnect:` refusal, `Script .* returned error`), else the last
  non-empty line.
- `count_routes(interface) -> int`: `netstat -rn -f inet` lines whose last
  column is the interface (0 if the interface is absent).
- `pin_from_probe(output) -> str | None`: `pin-sha256:[A-Za-z0-9+/=]+`.

### 9.4 `tunnel.py`: `TunnelManager`

Holds an in-memory `dict[id, TunnelState]` guarded by a lock, plus the runner
and registry. States:

```
disconnected --connect()--> connecting --<id>.iface appears, pid alive--> connected
connecting   --timeout / nonzero exit / failure line--> error(message)
connected    --disconnect()--> disconnecting --helper returns--> disconnected
connected    --pid gone (seen on refresh)--> error("tunnel dropped: <last log line>")
error        --connect() or disconnect()--> (as above)
```

`connect(id)` returns immediately after starting a worker thread and marking
`connecting`. The worker: probes and saves the pin if `servercert` is missing;
calls `runner.connect`; a nonzero exit is an immediate `error` with
`parse_failure(log)`; otherwise it polls every 0.5 s up to `CONNECT_TIMEOUT`
for `<id>.iface` with a live pid; on timeout calls
`runner.disconnect` and sets `error`. `connect_all()` starts one worker per
VPN that has credentials and is not already connecting or connected; it
returns the started ids and the skipped ids with reasons.

`disconnect(id)` marks `disconnecting`, runs the helper in a worker, then
marks `disconnected` and clears the message. `disconnect_all()` does that for
every id in `connected`, `connecting` or `error` with a live pid.

`refresh()` (called by every status read) reconciles in-memory state with the
disk: a pid file whose process is dead moves `connected` to `error("tunnel
dropped…")` and removes the pid file; a live pid with no in-memory record (app
restarted while tunnels stayed up) is adopted as `connected`.

`snapshot() -> list[VpnStatus]` with `id, name, server, authgroup, routes,
has_credentials, state, interface, ip, routes_count, message, since`.

### 9.5 API (`app/api/vpns.py`), all JSON, documented in flasgger docstrings

| Method | Path | Success | Errors |
|---|---|---|---|
| GET | `/api/v1/health` | 200 `{"status":"ok"}` | |
| GET | `/api/v1/vpns` | 200 `{"vpns":[VpnStatus…]}` | |
| GET | `/api/v1/vpns/<id>` | 200 `VpnStatus` | 404 unknown id |
| POST | `/api/v1/vpns/<id>/connect` | 202 `{"id","state":"connecting"}` | 404; 400 `missing credentials: VPN_X_PASSWORD`; 409 already connecting/connected |
| POST | `/api/v1/vpns/<id>/disconnect` | 202 `{"id","state":"disconnecting"}` | 404; 409 not connected |
| POST | `/api/v1/vpns/connect-all` | 202 `{"started":[ids],"skipped":[{"id","reason"}]}` | |
| POST | `/api/v1/vpns/disconnect-all` | 202 `{"started":[ids],"skipped":[{"id","reason"}]}` | |
| GET | `/api/v1/vpns/<id>/log?lines=50` | 200 `{"id","lines":[…]}` | 404 |
| GET | `/` | 200 HTML dashboard | |

Errors use `{"error": "<message>"}`. Passwords never appear in any response or
log line; the log endpoint serves openconnect's own output, which does not echo
the stdin password.

### 9.6 Dashboard (`templates/index.html`)

One page, no build step. Header with "Connect all" and "Disconnect all"
buttons and a last-refresh time. A table with one row per VPN: name and server,
status pill (grey disconnected, amber connecting/disconnecting, green
connected, red error), interface and IP, route count, Connect and Disconnect
buttons (disabled when not applicable), and an expandable "log" row that loads
the last 50 log lines. Rows with missing credentials show which variable is
missing instead of a Connect button. JavaScript polls `/api/v1/vpns` every 3 s
and after every action.

## 10. Error handling summary

| Situation | Behaviour |
|---|---|
| Missing username or password | 400 on connect, row shows the variable name; connect-all skips it |
| Wrong password | openconnect exits nonzero, log has `Login failed`; state `error("Login failed")` |
| Stored pin no longer matches | openconnect refuses; `error("Certificate … failed verification")`; message tells the user to delete `servercert` in `vpns.yaml` to re-probe |
| Server pushes full tunnel, no routes configured | wrapper exits 1; `error` with the refusal message |
| No `<id>.iface` within timeout | helper disconnect, `error("timed out after 30 s: <last log line>")` |
| Tunnel dies later | next refresh shows `error("tunnel dropped: …")`, pid file removed |
| Helper not installed or sudoers missing | `sudo -n` fails; `error("privilege helper not available: run sudo scripts/setup-privileges.sh")` |
| App restarted while tunnels up | pid files adopted as `connected` on first refresh |
| Bad `vpns.yaml` | app refuses to start, names the entry and field |

## 11. Testing

Unit (pytest, fake runner, temp dirs):

- registry: parse, validation errors, env credential mapping including `-`
  to `_`, `routes_arg()` triples, `save_servercert` preserves other keys.
- status: pid alive semantics (EPERM = alive), iface file parsing, failure-line
  parsing, route counting over canned `netstat` output, pin extraction.
- tunnel: full state machine with a fake runner that writes canned log text;
  timeout path; adoption on refresh; connect-all skip reasons.
- wrapper: run `scripts/vpnc-split.sh` with a stub vpnc-script that prints
  its environment; assert the three routing branches (server split, forced
  split, refusal), that the iface file is written on connect and removed on
  disconnect, and that `exec` passes `$@` through.
- API: Flask test client against a `TunnelManager` with the fake runner; every
  status code in section 9.5; passwords absent from all responses.

Manual acceptance (with Longin, after `sudo scripts/setup-privileges.sh`):

1. Fill `vpns.yaml` and `.env` for two real VPNs.
2. `uv run flask run`, open the dashboard, press Connect all.
3. Both rows turn green with different `utun` interfaces; `netstat -rn` shows
   only the internal networks on each interface and the default route still on
   `en0`.
4. Reach one internal host per VPN.
5. Disconnect all; interfaces and routes disappear; `state/` has no pid files.

Quality gates before each commit: `uv run ruff check .`, `uv run ruff format
--check .`, `uv run pytest`.

## 12. Open risks

- Cisco Secure Client's socket-filter system extension is loaded on this Mac.
  It is not expected to interfere with `utun` devices created by openconnect,
  but the manual acceptance test is the proof. If it does, quitting the Cisco
  client is the workaround.
- Two VPNs that route the same internal network cannot both win; the first
  route added stays. The dashboard shows route counts, not conflicts. v1
  documents this in the README rather than detecting it.
- Servers that require a second factor will stop at `--non-inter` and fail
  with a clear log line; v1 does not support them.
