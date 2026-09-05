"""Flask application factory."""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from flask import Flask
from flask_wtf import CSRFProtect

from .config import load_config
from .controller import ServerController
from .db import Database

csrf = CSRFProtect()


def create_app() -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    cfg = load_config()

    app = Flask(__name__)
    app.config["SECRET_KEY"] = cfg.SECRET_KEY
    app.config["LOGIN_MAX_ATTEMPTS"] = cfg.LOGIN_MAX_ATTEMPTS
    app.config["LOGIN_LOCKOUT_SECONDS"] = cfg.LOGIN_LOCKOUT_SECONDS
    app.config["INVITE_LIFETIME_HOURS"] = cfg.INVITE_LIFETIME_HOURS

    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=cfg.SESSION_LIFETIME_HOURS)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # Enable this (True) as soon as the app is served over HTTPS behind a reverse proxy.
    app.config["SESSION_COOKIE_SECURE"] = getattr(cfg, "SESSION_COOKIE_SECURE", False)

    csrf.init_app(app)

    instance_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instance")
    app.db = Database(instance_path)
    # Creates the initial admin account from instance/config.py only once
    # (does nothing if accounts already exist in the database).
    app.db.seed_admin_from_config(cfg.ADMIN_USERNAME, cfg.ADMIN_PASSWORD_HASH)

    app.controller = ServerController(cfg)
    app.controller.start_monitor()

    from .admin import admin_bp
    from .auth import auth_bp
    from .routes import main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(main_bp)

    return app
