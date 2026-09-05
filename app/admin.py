"""Managing single-use invitations, user registration, and account
administration (editing, deletion, activity review) — all accessible only to
the administrator (/admin page).
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
        flash("This account no longer exists.", "error")
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
        flash("This account no longer exists.", "error")
        return redirect(url_for("admin.dashboard"))

    new_username = request.form.get("username", "").strip()
    new_role = request.form.get("role", user.role)

    if new_role not in (ROLE_ADMIN, ROLE_USER):
        new_role = user.role

    if len(new_username) < MIN_USERNAME_LENGTH:
        flash(f"Username must contain at least {MIN_USERNAME_LENGTH} characters.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    # Prevents removing the admin role from the last remaining administrator:
    # otherwise nobody would be able to access the administration page.
    if user.role == ROLE_ADMIN and new_role != ROLE_ADMIN and db.count_admins() <= 1:
        flash("Cannot remove the administrator role: this is the last admin account.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    try:
        db.update_user(user_id, username=new_username, role=new_role)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    # If the admin edits their own account, the session must reflect the
    # change immediately (displayed name, role used by the decorators).
    if user_id == session.get("user_id"):
        session["username"] = new_username
        session["role"] = new_role

    flash("Account updated.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_password(user_id: int):
    db = current_app.db
    user = db.get_user_by_id(user_id)
    if user is None:
        flash("This account no longer exists.", "error")
        return redirect(url_for("admin.dashboard"))

    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")

    if len(password) < MIN_PASSWORD_LENGTH:
        flash(f"Password must contain at least {MIN_PASSWORD_LENGTH} characters.", "error")
    elif password != confirm:
        flash("Passwords do not match.", "error")
    else:
        db.set_password(user_id, generate_password_hash(password))
        flash("Password reset.", "success")

    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id: int):
    db = current_app.db
    user = db.get_user_by_id(user_id)
    if user is None:
        flash("This account no longer exists.", "error")
        return redirect(url_for("admin.dashboard"))

    if user_id == session.get("user_id"):
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    if user.role == ROLE_ADMIN and db.count_admins() <= 1:
        flash("Cannot delete the last administrator account.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    db.delete_user(user_id)
    flash(f"Account “{user.username}” deleted.", "success")
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
            error = f"Username must contain at least {MIN_USERNAME_LENGTH} characters."
        elif db.username_exists(username):
            error = "This username is already taken."
        elif len(password) < MIN_PASSWORD_LENGTH:
            error = f"Password must contain at least {MIN_PASSWORD_LENGTH} characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            # Marks the invitation as used atomically before creating the
            # account: if two requests arrive at the same time on the same
            # link, only one will be able to create an account.
            if not db.consume_invite(token, used_by=username):
                return render_template("register.html", invalid=True), 410

            db.create_user(username, generate_password_hash(password), invite.role)
            return redirect(url_for("auth.login"))

    return render_template("register.html", invalid=False, error=error, token=token)
