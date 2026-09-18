from flask import Blueprint, jsonify, request

api_bp = Blueprint("api", __name__)

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost"}


@api_bp.before_request
def require_same_origin():
    """Refuse a mutating request that a foreign page could have sent.

    The API has no authentication, so a page on another origin must not be
    able to start, change or delete a tunnel. Only the host and the Origin
    header are checked: a request without an Origin (curl, the tests) is not
    a browser request. Reads stay open.
    """
    if request.method not in MUTATING_METHODS:
        return None
    host = request.host
    if host.rsplit(":", 1)[0] not in LOCAL_HOSTNAMES:
        return jsonify({"error": f"refused: {request.method} for host {host}"}), 403
    origin = request.headers.get("Origin")
    if origin is not None and origin != f"http://{host}":
        return jsonify({"error": f"refused: {request.method} from origin {origin}"}), 403
    return None


from app.api import health, vpns  # noqa: E402, F401
