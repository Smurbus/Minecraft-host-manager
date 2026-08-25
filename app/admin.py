"""Gestion des invitations à usage unique, inscription des utilisateurs, et
administration des comptes (modification, suppression, consultation de
l'activité) — tout accessible uniquement à l'administrateur (page /admin).
"""

from __future__ import annotations

from werkzeug.security import generate_password_hash

from flask import (
    Blueprint,
    current_app,
    flash,
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
MIN_USERNAME_LENGTH = 3


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


@admin_bp.route("/admin/users/<int:user_id>", methods=["GET"])
@admin_required
def user_detail(user_id: int):
    db = current_app.db
    user = db.get_user_by_id(user_id)
    if user is None:
        flash("Ce compte n'existe plus.", "error")
        return redirect(url_for("admin.dashboard"))

    activity = db.list_activity_for_user(user_id)
    return render_template(
        "user_detail.html",
        user=user,
        activity=activity,
        is_self=user.id == session.get("user_id"),
        is_last_admin=user.role == ROLE_ADMIN and db.count_admins() <= 1,
    )


@admin_bp.route("/admin/users/<int:user_id>/update", methods=["POST"])
@admin_required
def update_user(user_id: int):
    db = current_app.db
    user = db.get_user_by_id(user_id)
    if user is None:
        flash("Ce compte n'existe plus.", "error")
        return redirect(url_for("admin.dashboard"))

    new_username = request.form.get("username", "").strip()
    new_role = request.form.get("role", user.role)

    if new_role not in (ROLE_ADMIN, ROLE_USER):
        new_role = user.role

    if len(new_username) < MIN_USERNAME_LENGTH:
        flash(f"Le nom d'utilisateur doit contenir au moins {MIN_USERNAME_LENGTH} caractères.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    # Empêche de retirer le rôle admin au dernier administrateur restant :
    # sans ça, plus personne ne pourrait accéder à la page d'administration.
    if user.role == ROLE_ADMIN and new_role != ROLE_ADMIN and db.count_admins() <= 1:
        flash("Impossible de retirer le rôle administrateur : c'est le dernier compte admin.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    try:
        db.update_user(user_id, username=new_username, role=new_role)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    # Si l'admin modifie son propre compte, la session doit refléter le
    # changement immédiatement (nom affiché, rôle utilisé par les décorateurs).
    if user_id == session.get("user_id"):
        session["username"] = new_username
        session["role"] = new_role

    flash("Compte mis à jour.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_password(user_id: int):
    db = current_app.db
    user = db.get_user_by_id(user_id)
    if user is None:
        flash("Ce compte n'existe plus.", "error")
        return redirect(url_for("admin.dashboard"))

    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")

    if len(password) < MIN_PASSWORD_LENGTH:
        flash(f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères.", "error")
    elif password != confirm:
        flash("Les mots de passe ne correspondent pas.", "error")
    else:
        db.set_password(user_id, generate_password_hash(password))
        flash("Mot de passe réinitialisé.", "success")

    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id: int):
    db = current_app.db
    user = db.get_user_by_id(user_id)
    if user is None:
        flash("Ce compte n'existe plus.", "error")
        return redirect(url_for("admin.dashboard"))

    if user_id == session.get("user_id"):
        flash("Vous ne pouvez pas supprimer votre propre compte.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    if user.role == ROLE_ADMIN and db.count_admins() <= 1:
        flash("Impossible de supprimer le dernier compte administrateur.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    db.delete_user(user_id)
    flash(f"Compte « {user.username} » supprimé.", "success")
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

        if not username or len(username) < MIN_USERNAME_LENGTH:
            error = f"Le nom d'utilisateur doit contenir au moins {MIN_USERNAME_LENGTH} caractères."
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
