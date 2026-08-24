"""Authentification par session pour l'interface web.

Les comptes utilisateurs (admin + comptes créés via invitation) sont
stockés dans la base SQLite (app/db.py). Un verrouillage temporaire est
appliqué après plusieurs échecs de connexion depuis la même adresse IP,
utile car l'application peut être exposée sur Internet.
"""

from __future__ import annotations

import threading
import time
from functools import wraps

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

auth_bp = Blueprint("auth", __name__)

_lock = threading.Lock()
_failed_attempts: dict[str, list[float]] = {}


def _client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def _is_locked_out(ip: str, max_attempts: int, lockout_seconds: int) -> bool:
    with _lock:
        attempts = _failed_attempts.get(ip, [])
        cutoff = time.monotonic() - lockout_seconds
        attempts = [t for t in attempts if t > cutoff]
        _failed_attempts[ip] = attempts
        return len(attempts) >= max_attempts


def _register_failure(ip: str) -> None:
    with _lock:
        _failed_attempts.setdefault(ip, []).append(time.monotonic())


def _clear_failures(ip: str) -> None:
    with _lock:
        _failed_attempts.pop(ip, None)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("auth.login", next=request.path))
        if session.get("role") != "admin":
            return render_template("error.html", message="Accès réservé à l'administrateur."), 403
        return view(*args, **kwargs)

    return wrapped


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    cfg = current_app.config
    error = None

    if request.method == "POST":
        ip = _client_ip()
        if _is_locked_out(ip, cfg["LOGIN_MAX_ATTEMPTS"], cfg["LOGIN_LOCKOUT_SECONDS"]):
            error = "Trop de tentatives échouées. Réessayez plus tard."
        else:
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            user = current_app.db.verify_password(username, password)
            if user is not None:
                _clear_failures(ip)
                session.clear()
                session["logged_in"] = True
                session["username"] = user.username
                session["role"] = user.role
                session.permanent = True
                next_url = request.args.get("next") or url_for("main.index")
                return redirect(next_url)

            _register_failure(ip)
            error = "Identifiants incorrects."

    return render_template("login.html", error=error)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
