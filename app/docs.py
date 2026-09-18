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
        {
            "name": "VPNs",
            "description": "List, connect and disconnect VPN tunnels, and edit their definitions",
        },
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
                        "description": (
                            "Networks forced into the tunnel when the server pushes a full tunnel."
                        ),
                    },
                    "servercert": {
                        "type": "string",
                        "nullable": True,
                        "example": "pin-sha256:0Yl6a3cSB6AQ8r5k7fT9m6yLxWvqNzR2pCd3eF4gH5I=",
                        "description": (
                            "Stored certificate pin, 'trusted-ca', or null before the first probe."
                        ),
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
            "VpnCreate": {
                "type": "object",
                "required": ["id", "server", "authgroup"],
                "description": (
                    "Fields a user owns. protocol stays in vpns.yaml and servercert is "
                    "written by the app after the first probe, so neither is accepted here."
                ),
                "properties": {
                    "id": {
                        "type": "string",
                        "pattern": "^[a-z0-9][a-z0-9_-]*$",
                        "example": "rica-hq",
                        "description": (
                            "Immutable: it names the env vars and the state files. "
                            "Renaming means delete and add."
                        ),
                    },
                    "name": {"type": "string", "example": "RICA head office"},
                    "server": {"type": "string", "example": "vpn.example.gov.rw"},
                    "authgroup": {"type": "string", "example": "Staff"},
                    "routes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "example": ["10.10.0.0/16"],
                        "description": "IPv4 CIDRs; a default route is rejected.",
                    },
                    "username": {"type": "string", "writeOnly": True},
                    "password": {
                        "type": "string",
                        "writeOnly": True,
                        "description": (
                            "Stored in .env only. No response, log line or page repeats it."
                        ),
                    },
                },
            },
            "VpnUpdate": {
                "type": "object",
                "description": (
                    "Any subset of the editable fields. An empty or missing username or "
                    "password keeps the stored one; a new server clears the servercert."
                ),
                "properties": {
                    "name": {"type": "string", "example": "RICA head office"},
                    "server": {"type": "string", "example": "vpn.example.gov.rw"},
                    "authgroup": {"type": "string", "example": "Staff"},
                    "routes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "example": ["10.10.0.0/16"],
                    },
                    "username": {"type": "string", "writeOnly": True},
                    "password": {"type": "string", "writeOnly": True},
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
