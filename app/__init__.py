import os
from pathlib import Path

from flask import Flask

from app.config import Config
from app.docs import init_docs
from app.services.registry import load_registry
from app.services.runner import SudoHelperRunner
from app.services.tunnel import TunnelManager


def build_manager(config: dict) -> TunnelManager:
    """The real manager: vpns.yaml + environment credentials + sudo helper."""
    registry = load_registry(config["VPNS_FILE"], os.environ)
    runner = SudoHelperRunner(
        config["HELPER"],
        connect_timeout=config["CONNECT_TIMEOUT"],
        disconnect_grace=config["DISCONNECT_GRACE"],
    )
    return TunnelManager(
        registry, runner, Path(config["STATE_DIR"]), connect_timeout=config["CONNECT_TIMEOUT"]
    )


def create_app(config_class: type[Config] = Config, manager: TunnelManager | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    # A bad vpns.yaml raises RegistryError here, so `flask run` fails loudly
    # with the offending entry named instead of serving an empty dashboard.
    app.extensions["tunnels"] = manager if manager is not None else build_manager(app.config)

    from app.api import api_bp

    app.register_blueprint(api_bp, url_prefix="/api/v1")
    init_docs(app)

    return app
