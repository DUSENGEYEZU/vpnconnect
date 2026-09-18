import logging
import os
import re
from pathlib import Path

from flask import Flask, render_template

from app.config import Config
from app.docs import init_docs
from app.services.registry import load_registry
from app.services.runner import SudoHelperRunner
from app.services.tunnel import TunnelManager

logger = logging.getLogger(__name__)

# scripts/setup-privileges.sh bakes the state dir into the installed helper.
HELPER_STATE_DIR = re.compile(r'^STATE_DIR="(.*)"$', re.MULTILINE)


def warn_if_state_dir_differs(helper_path: str, state_dir: str) -> None:
    """The helper derives every file path from its own baked STATE_DIR, so an
    app configured for another directory never sees the files it writes. Only
    ever a warning: the helper may not be installed yet.
    """
    try:
        baked = HELPER_STATE_DIR.search(Path(helper_path).read_text(errors="replace"))
        if baked is None or Path(baked.group(1)).resolve() == Path(state_dir).resolve():
            return
    except OSError:
        return
    logger.warning(
        "state dir mismatch: this app uses %s but %s was installed with %s; "
        "re-run 'sudo STATE_DIR=%s scripts/setup-privileges.sh'",
        state_dir,
        helper_path,
        baked.group(1),
        state_dir,
    )


def build_manager(config: dict) -> TunnelManager:
    """The real manager: vpns.yaml + environment credentials + sudo helper."""
    registry = load_registry(config["VPNS_FILE"], os.environ)
    warn_if_state_dir_differs(config["HELPER"], config["STATE_DIR"])
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

    @app.get("/")
    def index():
        return render_template("index.html")

    return app
