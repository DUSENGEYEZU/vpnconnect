"""VPN endpoints. Actions return 202 right away; poll GET /vpns for progress.

Definitions are edited here too: the body carries only the fields a user owns
and credentials are write-only, so no response repeats a password.
"""

from typing import Any

from flask import current_app, jsonify, request

from app.api import api_bp
from app.services.registry import DuplicateVpn, RegistryError
from app.services.tunnel import (
    InvalidTransition,
    MissingCredentials,
    TunnelManager,
    UnknownVpn,
)

MAX_LOG_LINES = 500
# Accepted request-body fields. servercert is written by the app after a probe
# and protocol is left to vpns.yaml, so neither can be set from the API.
EDITABLE_FIELDS = ("id", "name", "server", "authgroup", "routes", "username", "password")
STRING_FIELDS = ("id", "name", "server", "authgroup", "username", "password")


class BadRequest(ValueError):
    """The request body has the wrong shape; the message names the field."""


def manager() -> TunnelManager:
    return current_app.extensions["tunnels"]


def error(message: str, status: int):
    return jsonify({"error": message}), status


def body_fields(vpn_id: str | None = None) -> dict[str, Any]:
    """The request body, checked for shape only; parse_vpn checks the values.

    On an update, vpn_id is the id from the path: an 'id' in the body has to
    repeat it, because the id is immutable.
    """
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    for key in body:
        if key not in EDITABLE_FIELDS:
            raise BadRequest(f"unknown field: {key!r}")
    for key in STRING_FIELDS:
        if key in body and not isinstance(body[key], str):
            raise BadRequest(f"{key!r} must be a string")
    routes = body.get("routes")
    if routes is not None and (
        not isinstance(routes, list) or any(not isinstance(route, str) for route in routes)
    ):
        raise BadRequest("'routes' must be a list of CIDR strings")
    if vpn_id is not None and body.get("id", vpn_id) != vpn_id:
        raise BadRequest("id cannot be changed; delete this VPN and add it again")
    return body


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


@api_bp.post("/vpns")
def create_vpn():
    """Add a VPN to vpns.yaml and its credentials to .env.
    ---
    tags:
      - VPNs
    requestBody:
      required: true
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/VpnCreate'
    responses:
      201:
        description: The VPN as GET /vpns/{vpn_id} returns it.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/VpnStatus'
      400:
        description: A field is missing or invalid; the message names it.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Error'
      409:
        description: That id is already in vpns.yaml.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Error'
    """
    try:
        return jsonify(manager().create_vpn(body_fields())), 201
    except BadRequest as exc:
        return error(str(exc), 400)
    except DuplicateVpn as exc:
        return error(str(exc), 409)
    except RegistryError as exc:
        return error(str(exc), 400)


@api_bp.put("/vpns/<vpn_id>")
def update_vpn(vpn_id: str):
    """Change a VPN. Fields left out keep their value; an empty username or
    password keeps the stored one. The id cannot change.
    ---
    tags:
      - VPNs
    parameters:
      - in: path
        name: vpn_id
        required: true
        schema:
          type: string
    requestBody:
      required: true
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/VpnUpdate'
    responses:
      200:
        description: The updated VPN.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/VpnStatus'
      400:
        description: A field is invalid, or the body tries to change the id.
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
        description: The tunnel is not disconnected.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Error'
    """
    try:
        return jsonify(manager().edit_vpn(vpn_id, body_fields(vpn_id)))
    except BadRequest as exc:
        return error(str(exc), 400)
    except UnknownVpn as exc:
        return error(str(exc), 404)
    except InvalidTransition as exc:
        return error(str(exc), 409)
    except RegistryError as exc:
        return error(str(exc), 400)


@api_bp.delete("/vpns/<vpn_id>")
def delete_vpn(vpn_id: str):
    """Remove a VPN, its credentials and its log file.
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
      204:
        description: Removed.
      404:
        description: Unknown VPN id.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Error'
      409:
        description: The tunnel is not disconnected.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Error'
    """
    try:
        manager().delete_vpn(vpn_id)
    except UnknownVpn as exc:
        return error(str(exc), 404)
    except InvalidTransition as exc:
        return error(str(exc), 409)
    return "", 204
