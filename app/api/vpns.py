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
