"""
Main Flask Application for YouTube AI Autopilot Dashboard.

Implements Blueprint-based architecture, Flask-Login authentication,
CSRF protection, rate limiting, custom Arabic Jinja filters,
WebSocket real-time updates, and error handling.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, UserMixin
from flask_socketio import SocketIO
from flask_wtf.csrf import CSRFProtect

from src.dashboard.db import (
    add_audit_log,
    get_db_connection,
    get_system_state,
    get_user_by_username,
    init_db,
)


# Path definitions
BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


# Initialize extensions
csrf = CSRFProtect()
login_manager = LoginManager()
socketio = SocketIO()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["300 per hour", "50 per minute"],
    storage_uri="memory://",
)


class AdminUser(UserMixin):
    """User representation for Flask-Login session management."""

    def __init__(self, user_id: int, username: str, role: str = "admin") -> None:
        self.id = user_id
        self.username = username
        self.role = role

    def get_id(self) -> str:
        """Return username as unique session identifier."""
        return self.username


@login_manager.user_loader
def load_user(user_id: str) -> Optional[AdminUser]:
    """Retrieve user object for active session."""
    user_record = get_user_by_username(user_id)
    if user_record:
        return AdminUser(
            user_id=user_record["id"],
            username=user_record["username"],
            role=user_record["role"],
        )
    return None


def register_template_filters(app: Flask) -> None:
    """Register custom Jinja2 template filters for Arabic formatting."""

    @app.template_filter("arabic_number")
    def arabic_number_filter(value: Any) -> str:
        """Format numbers with comma separators and Arabic numerals optionally."""
        if value is None:
            return "0"
        try:
            val_int = int(value)
            return f"{val_int:,}"
        except (ValueError, TypeError):
            return str(value)

    @app.template_filter("arabic_date")
    def arabic_date_filter(value: Any) -> str:
        """Format datetime into a readable Arabic timestamp."""
        if not value:
            return "غير محدد"
        try:
            if isinstance(value, str):
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            elif isinstance(value, datetime):
                dt = value
            else:
                return str(value)

            months_ar = [
                "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
                "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"
            ]
            month_name = months_ar[dt.month - 1]
            return f"{dt.day} {month_name} {dt.year} - {dt.strftime('%H:%M')}"
        except Exception:
            return str(value)

    @app.template_filter("format_currency")
    def format_currency_filter(value: Any) -> str:
        """Format monetary amount in USD with Arabic currency label."""
        try:
            num = float(value or 0.0)
            return f"${num:,.2f}"
        except (ValueError, TypeError):
            return "$0.00"

    @app.template_filter("status_ar")
    def status_ar_filter(status: str) -> str:
        """Translate status codes into clean Arabic labels."""
        status_map = {
            "draft": "مسودة",
            "producing": "قيد الإنتاج",
            "produced": "تم الإنتاج",
            "uploaded": "تم الرفع",
            "scheduled": "مجدول",
            "published": "تم النشر",
            "failed": "فشل",
            "pending": "في الانتظار",
            "in_progress": "قيد التنفيذ",
            "completed": "مكتمل",
            "skipped": "تم التخطي",
            "running": "يعمل حالياً",
            "queued": "في قائمة الانتظار",
            "idea": "فكرة مقترحة",
            "approved": "تمت الموافقة",
            "scripted": "تم كتابة النص",
            "rejected": "مرفوض",
            "healthy": "سليم",
            "warning": "تحذير",
            "critical": "حرج للغاية",
            "error": "خطأ",
            "active": "نشط",
            "inactive": "معطل",
        }
        return status_map.get(str(status).lower(), str(status))

    @app.template_filter("badge_class")
    def badge_class_filter(status: str) -> str:
        """Return CSS badge modifier class for given status."""
        badge_map = {
            "published": "badge-success",
            "completed": "badge-success",
            "healthy": "badge-success",
            "approved": "badge-success",
            "scheduled": "badge-info",
            "uploaded": "badge-info",
            "producing": "badge-warning",
            "running": "badge-warning",
            "in_progress": "badge-warning",
            "scripted": "badge-info",
            "pending": "badge-secondary",
            "queued": "badge-secondary",
            "draft": "badge-secondary",
            "idea": "badge-secondary",
            "failed": "badge-danger",
            "critical": "badge-danger",
            "error": "badge-danger",
            "warning": "badge-warning",
        }
        return badge_map.get(str(status).lower(), "badge-secondary")


def register_error_handlers(app: Flask) -> None:
    """Register HTTP error pages and JSON responses."""

    @app.errorhandler(404)
    def page_not_found(error: Any) -> Tuple[str, int]:
        """Handle 404 Not Found error."""
        if request.path.startswith("/api/"):
            return jsonify({"status": "error", "message": "المورد المطلوب غير موجود (404)"}), 404
        return render_template("dashboard/404.html", error=error), 404

    @app.errorhandler(500)
    def internal_server_error(error: Any) -> Tuple[str, int]:
        """Handle 500 Internal Server Error."""
        if request.path.startswith("/api/"):
            return jsonify({"status": "error", "message": "حدث خطأ غير متوقع في الخادم (500)"}), 500
        return render_template("dashboard/500.html", error=error), 500

    @app.errorhandler(429)
    def ratelimit_handler(error: Any) -> Tuple[str, int]:
        """Handle 429 Too Many Requests rate-limiting error."""
        if request.path.startswith("/api/"):
            return jsonify({
                "status": "error",
                "message": "تم تجاوز الحد المسموح من الطلبات. يرجى الانتظار قليلاً.",
            }), 429
        return render_template("dashboard/404.html", error_title="تجاوز معدل الطلبات", error=error), 429


def create_app(test_config: Optional[Dict[str, Any]] = None) -> Flask:
    """Application factory for the YouTube AI Autopilot Dashboard."""
    app = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
    )

    # Configuration
    secret_key = os.environ.get("DASHBOARD_SECRET_KEY")
    if not secret_key:
        secret_key = "yt-ai-autopilot-dev-secret-key-change-in-production"

    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=False,  # Set to True when SSL/TLS is active
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # 16 MB maximum upload
        WTF_CSRF_TIME_LIMIT=3600 * 24,
    )

    if test_config:
        app.config.update(test_config)

    # Initialize extensions with app
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "dashboard.login"
    login_manager.login_message = "يرجى تسجيل الدخول للوصول إلى لوحة التحكم."
    login_manager.login_message_category = "warning"

    socketio.init_app(app, cors_allowed_origins="*", async_mode="threading")

    # Ensure database is initialized
    init_db()

    # Register Jinja filters and error handlers
    register_template_filters(app)
    register_error_handlers(app)

    # Register dashboard blueprint
    from src.dashboard.routes import dashboard_bp
    app.register_blueprint(dashboard_bp, url_prefix="")

    return app


# Application entrypoint
app = create_app()


# SocketIO event handlers
@socketio.on("connect")
def handle_socket_connect() -> None:
    """Handle incoming WebSocket connection and send current operational status."""
    state = get_system_state()
    socketio.emit("status_snapshot", {
        "is_paused": state["is_paused"],
        "is_emergency_stopped": state["is_emergency_stopped"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@socketio.on("request_sync")
def handle_request_sync() -> None:
    """Broadcast latest system metrics to requesting client."""
    state = get_system_state()
    socketio.emit("system_state_update", {
        "is_paused": state["is_paused"],
        "is_emergency_stopped": state["is_emergency_stopped"],
        "last_heartbeat": state["last_heartbeat"],
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False)
