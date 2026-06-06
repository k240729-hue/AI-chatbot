"""Authentication helpers — NIC-based login, role-based access."""
from functools import wraps
from flask import session, redirect, url_for, jsonify, request


def current_user():
    return session.get("user")


def is_logged_in():
    return "user" in session


def is_admin():
    u = current_user()
    return u is not None and u.get("role") == "admin"


def login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not is_logged_in():
            if request.path.startswith("/api/") or request.path == "/chat":
                return jsonify({"error": "Not authenticated", "redirect": "/login"}), 401
            return redirect(url_for("login"))
        return f(*a, **k)
    return wrap


def admin_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not is_logged_in():
            if request.path.startswith("/api/"):
                return jsonify({"error": "Not authenticated"}), 401
            return redirect(url_for("login"))
        if not is_admin():
            if request.path.startswith("/api/"):
                return jsonify({"error": "Admin only"}), 403
            return redirect(url_for("dashboard"))
        return f(*a, **k)
    return wrap
