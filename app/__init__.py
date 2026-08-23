"""Application factory Flask."""

from __future__ import annotations

import logging
from datetime import timedelta

from flask import Flask
from flask_wtf import CSRFProtect

from .config import load_config
from .controller import ServerController

csrf = CSRFProtect()


def create_app() -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    cfg = load_config()

    app = Flask(__name__)
    app.config["SECRET_KEY"] = cfg.SECRET_KEY
    app.config["ADMIN_USERNAME"] = cfg.ADMIN_USERNAME
    app.config["ADMIN_PASSWORD_HASH"] = cfg.ADMIN_PASSWORD_HASH
    app.config["LOGIN_MAX_ATTEMPTS"] = cfg.LOGIN_MAX_ATTEMPTS
    app.config["LOGIN_LOCKOUT_SECONDS"] = cfg.LOGIN_LOCKOUT_SECONDS

    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=cfg.SESSION_LIFETIME_HOURS)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # A activer (True) dès que l'app est servie en HTTPS derrière un reverse proxy.
    app.config["SESSION_COOKIE_SECURE"] = getattr(cfg, "SESSION_COOKIE_SECURE", False)

    csrf.init_app(app)

    app.controller = ServerController(cfg)
    app.controller.start_monitor()

    from .auth import auth_bp
    from .routes import main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    return app
