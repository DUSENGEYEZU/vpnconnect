from flask import Blueprint

api_bp = Blueprint("api", __name__)

from app.api import health  # noqa: E402, F401
