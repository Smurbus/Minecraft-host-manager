"""Gestion des invitations à usage unique et inscription des utilisateurs.

Accessible uniquement à l'administrateur (page /admin), pour générer des
liens d'invitation (/register/<token>) permettant à d'autres personnes de
créer leur propre compte, sans jamais partager le compte admin.
"""

from __future__ import annotations

from werkzeug.security import generate_password_hash

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .auth import admin_required
from .db import ROLE_ADMIN, ROLE_USER

admin_bp = Blueprint("admin", __name__)

MIN_PASSWORD_LENGTH = 8


@admin_bp.route("/admin", methods=["GET"])
@admin_required
def dashboard():
    db = current_app.db
    invites = db.list_invites()
    users = db.list_users()
    return render_template(
        "admin.html",
        invites=invites,
        users=users,
        invite_lifetime_hours=current_app.config["INVITE_LIFETIME_HOURS"],
    )


@admin_bp.route("/admin/invites", methods=["POST"])
@admin_required
def create_invite():
    db = current_app.db
    role = request.form.get("role", ROLE_USER)
    if role not in (ROLE_ADMIN, ROLE_USER):
        role = ROLE_USER
    db.create_invite(
        created_by=session["username"],
        role=role,
        lifetime_hours=current_app.config["INVITE_LIFETIME_HOURS"],
    )
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/register/<token>", methods=["GET", "POST"])
def register(token: str):
    db = current_app.db
    invite = db.get_invite_by_token(token)
    error = None

    if invite is None or not invite.is_valid():
        return render_template("register.html", invalid=True), 410

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not username or len(username) < 3:
            error = "Le nom d'utilisateur doit contenir au moins 3 caractères."
        elif db.username_exists(username):
            error = "Ce nom d'utilisateur est déjà pris."
        elif len(password) < MIN_PASSWORD_LENGTH:
            error = f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."
        elif password != confirm:
            error = "Les mots de passe ne correspondent pas."
        else:
            # Marque l'invitation comme utilisée de façon atomique avant de
            # créer le compte : si deux requêtes arrivent en même temps sur
            # le même lien, une seule pourra créer un compte.
            if not db.consume_invite(token, used_by=username):
                return render_template("register.html", invalid=True), 410

            db.create_user(username, generate_password_hash(password), invite.role)
            return redirect(url_for("auth.login"))

    return render_template("register.html", invalid=False, error=error, token=token)
