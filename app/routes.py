"""Routes principales : page d'accueil et API JSON utilisée par le
JavaScript côté client (statut, démarrage, arrêt)."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request

from .auth import login_required

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@login_required
def index():
    return render_template("index.html", username=None)


@main_bp.route("/api/status")
@login_required
def api_status():
    controller = current_app.controller
    return jsonify(controller.get_status())


@main_bp.route("/api/start", methods=["POST"])
@login_required
def api_start():
    controller = current_app.controller
    started = controller.start()
    if not started:
        return jsonify({"ok": False, "reason": "busy"}), 409
    return jsonify({"ok": True}), 202


@main_bp.route("/api/stop", methods=["POST"])
@login_required
def api_stop():
    controller = current_app.controller
    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force", False))

    result = controller.stop(force=force)

    if result.get("confirm_required"):
        return jsonify(result), 200
    if not result.get("started"):
        return jsonify({"ok": False, "reason": result.get("reason", "busy")}), 409
    return jsonify({"ok": True}), 202
