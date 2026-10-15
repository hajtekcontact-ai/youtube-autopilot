"""
Route definitions and handlers for the YouTube AI Autopilot Dashboard.

Provides full web views, RESTful JSON APIs, database querying,
pagination, filtering, secure authentication flows, and OAuth handshakes.
"""

from __future__ import annotations

import json
import os
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from src.dashboard.db import (
    add_audit_log,
    get_7day_analytics,
    get_analytics_summary,
    get_api_quotas,
    get_cloud_resources,
    get_content_ideas,
    get_db_connection,
    get_experiments,
    get_overview_stats,
    get_pipeline_jobs,
    get_publishing_calendar,
    get_recent_activity,
    get_revenue_data,
    get_settings,
    get_system_errors,
    get_system_state,
    get_today_schedule,
    get_user_by_username,
    get_video_by_id,
    get_videos,
    retry_error,
    save_settings,
    set_system_state,
    update_user_password,
)

# Blueprint creation
dashboard_bp = Blueprint("dashboard", __name__)


def get_client_ip() -> str:
    """Extract client IP address safely from request headers."""
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


# ==============================================================================
# Authentication Views
# ==============================================================================

@dashboard_bp.route("/login", methods=["GET", "POST"])
def login() -> Union[str, Response]:
    """Handle administrator authentication and session generation."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.overview"))

    error_message: Optional[str] = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user_data = get_user_by_username(username)
        is_valid = False

        if user_data:
            stored_hash = user_data["password_hash"]
            # Support werkzeug hash or direct match if dev mode
            if stored_hash.startswith(("scrypt:", "pbkdf2:", "argon2:")):
                is_valid = check_password_hash(stored_hash, password)
            elif stored_hash == password:
                is_valid = True
                # Upgrade hash on first login
                update_user_password(username, generate_password_hash(password))

        if is_valid and user_data:
            from src.dashboard.app import AdminUser
            admin_user = AdminUser(
                user_id=user_data["id"],
                username=user_data["username"],
                role=user_data["role"],
            )
            login_user(admin_user, remember=True)
            add_audit_log(
                event_type="auth_login_success",
                ip_address=get_client_ip(),
                user_agent=request.user_agent.string,
                details=f"تسجيل دخول ناجح للمستخدم: {username}",
            )
            flash("مرحباً بك! تم تسجيل الدخول بنجاح إلى لوحة التحكم.", "success")
            next_url = request.args.get("next")
            if next_url and next_url.startswith("/"):
                return redirect(next_url)
            return redirect(url_for("dashboard.overview"))
        else:
            error_message = "اسم المستخدم أو كلمة المرور غير صحيحة. يرجى المحاولة مجدداً."
            add_audit_log(
                event_type="auth_login_failed",
                ip_address=get_client_ip(),
                user_agent=request.user_agent.string,
                details=f"محاولة فاشلة لتسجيل الدخول باسم: {username}",
            )

    return render_template("dashboard/login.html", error=error_message)


@dashboard_bp.route("/logout")
@login_required
def logout() -> Response:
    """Terminate current user session and redirect to login page."""
    username = current_user.username if hasattr(current_user, "username") else "admin"
    add_audit_log(
        event_type="auth_logout",
        ip_address=get_client_ip(),
        user_agent=request.user_agent.string,
        details=f"تسجيل خروج للمستخدم: {username}",
    )
    logout_user()
    flash("تم تسجيل الخروج بنجاح. نتمنى لك يوماً سعيداً!", "info")
    return redirect(url_for("dashboard.login"))


# ==============================================================================
# Main Dashboard & Feature Views
# ==============================================================================

@dashboard_bp.route("/")
@login_required
def overview() -> str:
    """Render the comprehensive operational overview page."""
    stats = get_overview_stats()
    today_schedule = get_today_schedule()
    recent_activity = get_recent_activity(limit=8)
    weekly_analytics = get_7day_analytics()
    system_state = get_system_state()

    return render_template(
        "dashboard/overview.html",
        stats=stats,
        schedule=today_schedule,
        activity=recent_activity,
        weekly_analytics=weekly_analytics,
        system_state=system_state,
        current_page="overview",
    )


@dashboard_bp.route("/calendar")
@login_required
def calendar() -> str:
    """Render the automated publishing schedule calendar."""
    schedule_items = get_publishing_calendar()
    settings = get_settings()
    return render_template(
        "dashboard/calendar.html",
        schedule=schedule_items,
        settings=settings,
        current_page="calendar",
    )


@dashboard_bp.route("/videos")
@login_required
def videos() -> str:
    """Render the video library page with filtering, search, and pagination."""
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    per_page = 12
    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "all").strip()
    view_mode = request.args.get("view", "grid").strip()

    video_list, total_count = get_videos(
        page=page,
        per_page=per_page,
        search=search,
        status_filter=status_filter,
    )

    total_pages = max(1, math.ceil(total_count / per_page))

    return render_template(
        "dashboard/videos.html",
        videos=video_list,
        current_page_num=page,
        total_pages=total_pages,
        total_count=total_count,
        search_query=search,
        status_filter=status_filter,
        view_mode=view_mode,
        current_page="videos",
    )


@dashboard_bp.route("/content", methods=["GET", "POST"])
@login_required
def content() -> Union[str, Response]:
    """Render AI Content Manager and handle idea additions / status updates."""
    if request.method == "POST":
        action = request.form.get("action")
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if action == "add_idea":
            title = request.form.get("title", "").strip()
            topic = request.form.get("topic", "").strip()
            niche = request.form.get("niche", "science_technology_education").strip()
            hook = request.form.get("hook", "").strip()
            outline = request.form.get("outline", "").strip()

            if title and topic:
                conn = get_db_connection()
                conn.execute(
                    """
                    INSERT INTO content_ideas (title, topic, niche, hook, script_outline, status, ai_score, created_at)
                    VALUES (?, ?, ?, ?, ?, 'idea', 8.5, ?)
                    """,
                    (title, topic, niche, hook, outline, now_str),
                )
                conn.commit()
                conn.close()
                flash("تمت إضافة الفكرة الجديدة بنجاح إلى جدول التحضير.", "success")
            else:
                flash("يرجى ملء جميع الحقول الإلزامية للفكرة.", "danger")

        elif action == "update_status":
            idea_id = request.form.get("idea_id")
            new_status = request.form.get("status")
            if idea_id and new_status:
                conn = get_db_connection()
                conn.execute("UPDATE content_ideas SET status = ? WHERE id = ?", (new_status, idea_id))
                conn.commit()
                conn.close()
                flash("تم تحديث حالة الفكرة بنجاح.", "success")

        return redirect(url_for("dashboard.content"))

    ideas = get_content_ideas()
    return render_template(
        "dashboard/content.html",
        ideas=ideas,
        current_page="content",
    )


@dashboard_bp.route("/pipeline")
@login_required
def pipeline() -> str:
    """Render production pipeline status and live rendering progress."""
    jobs = get_pipeline_jobs()
    system_state = get_system_state()
    return render_template(
        "dashboard/pipeline.html",
        jobs=jobs,
        system_state=system_state,
        current_page="pipeline",
    )


@dashboard_bp.route("/analytics")
@login_required
def analytics() -> str:
    """Render detailed YouTube performance analytics."""
    data = get_analytics_summary()
    return render_template(
        "dashboard/analytics.html",
        totals=data["totals"],
        snapshots=data["snapshots"],
        current_page="analytics",
    )


@dashboard_bp.route("/revenue")
@login_required
def revenue() -> str:
    """Render financial and channel monetization metrics."""
    revenue_records = get_revenue_data()
    total_net = sum(r["net_profit"] for r in revenue_records)
    total_costs = sum(r["api_costs"] for r in revenue_records)
    total_ad = sum(r["ad_revenue"] for r in revenue_records)
    total_shorts = sum(r["shorts_feed_revenue"] for r in revenue_records)

    return render_template(
        "dashboard/revenue.html",
        records=revenue_records,
        total_net=total_net,
        total_costs=total_costs,
        total_ad=total_ad,
        total_shorts=total_shorts,
        current_page="revenue",
    )


@dashboard_bp.route("/experiments")
@login_required
def experiments() -> str:
    """Render A/B testing and growth strategy experiments."""
    exp_list = get_experiments()
    return render_template(
        "dashboard/experiments.html",
        experiments=exp_list,
        current_page="experiments",
    )


@dashboard_bp.route("/errors", methods=["GET", "POST"])
@login_required
def errors() -> Union[str, Response]:
    """Render system error log and handle error retries."""
    if request.method == "POST":
        error_id_raw = request.form.get("error_id")
        if error_id_raw:
            try:
                error_id = int(error_id_raw)
                success = retry_error(error_id)
                if success:
                    flash(f"تمت إعادة جدولة المهمة المرتبطة بالخطأ رقم #{error_id} بنجاح.", "success")
                else:
                    flash("لم يتم العثور على سجل الخطأ المحدد.", "warning")
            except ValueError:
                flash("معرف خطأ غير صالح.", "danger")
        return redirect(url_for("dashboard.errors"))

    severity = request.args.get("severity", "all")
    module = request.args.get("module", "all")
    unresolved_only = request.args.get("unresolved", "0") == "1"

    error_list = get_system_errors(
        severity=severity,
        module=module,
        unresolved_only=unresolved_only,
    )

    return render_template(
        "dashboard/errors.html",
        errors=error_list,
        selected_severity=severity,
        selected_module=module,
        unresolved_only=unresolved_only,
        current_page="errors",
    )


@dashboard_bp.route("/resources")
@login_required
def resources() -> str:
    """Render cloud infrastructure and computational resource usage."""
    resource_list = get_cloud_resources()
    total_cost = sum(r["cost_monthly_usd"] for r in resource_list)
    return render_template(
        "dashboard/resources.html",
        resources=resource_list,
        total_monthly_cost=total_cost,
        current_page="resources",
    )


@dashboard_bp.route("/quotas")
@login_required
def quotas() -> str:
    """Render API consumption meters and rate quota limits."""
    quota_list = get_api_quotas()
    return render_template(
        "dashboard/quotas.html",
        quotas=quota_list,
        current_page="quotas",
    )


@dashboard_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings() -> Union[str, Response]:
    """Render application settings and handle configuration updates and file uploads."""
    if request.method == "POST":
        # Handle file upload if provided
        uploaded_file = request.files.get("credentials_file")
        if uploaded_file and uploaded_file.filename:
            filename = secure_filename(uploaded_file.filename)
            if filename.lower().endswith((".json", ".env", ".txt")):
                target_dir = Path(__file__).resolve().parent.parent.parent / "config"
                target_dir.mkdir(parents=True, exist_ok=True)
                uploaded_file.save(str(target_dir / filename))
                flash(f"تم رفع ملف التهيئة '{filename}' بنجاح وحفظه في مجلد config.", "success")
                add_audit_log(
                    event_type="file_upload",
                    ip_address=get_client_ip(),
                    user_agent=request.user_agent.string,
                    details=f"رفع ملف إعدادات: {filename}",
                )
            else:
                flash("امتداد الملف غير مدعوم. يرجى رفع ملفات .json فقط.", "danger")

        # Update key-value settings
        keys_to_update = [
            "channel_niche",
            "content_language",
            "tts_voice",
            "shorts_per_day",
            "long_videos_per_day",
            "publish_timezone",
            "auto_publish_enabled",
            "gemini_model",
            "notify_on_publish",
            "notify_on_error",
            "notification_email",
            "cost_kill_switch",
            "max_daily_api_cost_usd",
            "auto_retry_failed_jobs",
        ]

        updated_dict: Dict[str, str] = {}
        for key in keys_to_update:
            if key in request.form:
                updated_dict[key] = request.form[key].strip()

        if updated_dict:
            save_settings(updated_dict)
            flash("تم حفظ جميع تعديلات الإعدادات بنجاح.", "success")
            add_audit_log(
                event_type="settings_update",
                ip_address=get_client_ip(),
                user_agent=request.user_agent.string,
                details="تحديث إعدادات النظام ولوحة التحكم",
            )

        return redirect(url_for("dashboard.settings"))

    current_settings = get_settings()

    # Integrations status indicators
    integrations_status = {
        "youtube": bool(os.environ.get("YOUTUBE_CLIENT_ID") and os.environ.get("YOUTUBE_CLIENT_SECRET")),
        "gemini": bool(os.environ.get("GEMINI_API_KEY")),
        "tts": True,  # Edge TTS is built-in
        "notifications": bool(os.environ.get("NOTIFICATION_EMAIL") or os.environ.get("SMTP_SERVER")),
    }

    return render_template(
        "dashboard/settings.html",
        settings=current_settings,
        integrations=integrations_status,
        current_page="settings",
    )


@dashboard_bp.route("/security", methods=["GET", "POST"])
@login_required
def security() -> Union[str, Response]:
    """Render security audit logs, account credentials, and emergency switches."""
    if request.method == "POST":
        action = request.form.get("action")

        if action == "change_password":
            current_pw = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            confirm_pw = request.form.get("confirm_password", "")

            user_data = get_user_by_username(current_user.username)
            if not user_data:
                flash("تعذر التحقق من بيانات المستخدم الحالي.", "danger")
            elif not check_password_hash(user_data["password_hash"], current_pw) and user_data["password_hash"] != current_pw:
                flash("كلمة المرور الحالية غير صحيحة.", "danger")
            elif len(new_pw) < 8:
                flash("يجب ألا تقل كلمة المرور الجديدة عن 8 أحرف وأرقام.", "danger")
            elif new_pw != confirm_pw:
                flash("كلمة المرور الجديدة غير متطابقة مع التأكيد.", "danger")
            else:
                new_hash = generate_password_hash(new_pw)
                update_user_password(current_user.username, new_hash)
                flash("تم تحديث كلمة المرور بنجاح وبشكل آمن.", "success")
                add_audit_log(
                    event_type="password_change",
                    ip_address=get_client_ip(),
                    user_agent=request.user_agent.string,
                    details=f"تغيير كلمة المرور للمستخدم: {current_user.username}",
                )

        return redirect(url_for("dashboard.security"))

    system_state = get_system_state()
    audit_logs = get_recent_activity(limit=25)

    return render_template(
        "dashboard/security.html",
        system_state=system_state,
        audit_logs=audit_logs,
        current_page="security",
    )


# ==============================================================================
# JSON REST API Endpoints
# ==============================================================================

@dashboard_bp.route("/api/status", methods=["GET"])
@login_required
def api_status() -> Response:
    """Return JSON operational health status and core metrics."""
    state = get_system_state()
    stats = get_overview_stats()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    return jsonify({
        "status": "success",
        "timestamp": now_str,
        "is_paused": state["is_paused"],
        "is_emergency_stopped": state["is_emergency_stopped"],
        "last_heartbeat": state["last_heartbeat"] or now_str,
        "total_videos": stats["total_videos"],
        "published_today": stats["published_today"],
        "pending_videos": stats["pending_videos"],
        "active_errors": stats["active_errors"],
        "quota_percent": stats["quota_percent"],
        "success_rate": stats["success_rate"],
        "system_health": "critical" if state["is_emergency_stopped"] else ("warning" if state["is_paused"] or stats["active_errors"] > 0 else "healthy"),
    })


@dashboard_bp.route("/api/pause", methods=["POST"])
@login_required
def api_pause() -> Response:
    """Pause automated production and scheduled publishing jobs."""
    set_system_state(is_paused=True)
    add_audit_log(
        event_type="system_paused",
        ip_address=get_client_ip(),
        user_agent=request.user_agent.string,
        details="إيقاف مؤقت للعمليات التلقائية بواسطة المسؤول",
    )
    return jsonify({
        "status": "success",
        "message": "تم إيقاف النشر والإنتاج التلقائي مؤقتاً.",
        "is_paused": True,
    })


@dashboard_bp.route("/api/resume", methods=["POST"])
@login_required
def api_resume() -> Response:
    """Resume automated publishing and pipeline processing."""
    set_system_state(is_paused=False, is_emergency_stopped=False)
    add_audit_log(
        event_type="system_resumed",
        ip_address=get_client_ip(),
        user_agent=request.user_agent.string,
        details="استئناف العمليات التلقائية بواسطة المسؤول",
    )
    return jsonify({
        "status": "success",
        "message": "تم استئناف النشر والإنتاج التلقائي بنجاح.",
        "is_paused": False,
        "is_emergency_stopped": False,
    })


@dashboard_bp.route("/api/emergency-stop", methods=["POST"])
@login_required
def api_emergency_stop() -> Response:
    """Trigger immediate emergency shutdown on all cloud and rendering tasks."""
    set_system_state(is_paused=True, is_emergency_stopped=True)
    add_audit_log(
        event_type="emergency_stop_triggered",
        ip_address=get_client_ip(),
        user_agent=request.user_agent.string,
        details="تفعيل زر إيقاف الطوارئ الفوري! تم تجميد كافة العمليات.",
    )
    return jsonify({
        "status": "success",
        "message": "تم تفعيل إيقاف الطوارئ الفوري! تم تجميد جميع خطوط الإنتاج والرفع السحابي.",
        "is_emergency_stopped": True,
        "is_paused": True,
    })


@dashboard_bp.route("/api/errors/retry/<int:error_id>", methods=["POST"])
@login_required
def api_retry_error(error_id: int) -> Response:
    """API endpoint to trigger job retry for a specific error ID."""
    success = retry_error(error_id)
    if success:
        add_audit_log(
            event_type="error_retry",
            ip_address=get_client_ip(),
            user_agent=request.user_agent.string,
            details=f"إعادة محاولة تنفيذ المهمة للخطأ رقم #{error_id}",
        )
        return jsonify({"status": "success", "message": f"تمت إعادة جدولة المهمة #{error_id} بنجاح."})
    return jsonify({"status": "error", "message": "لم يتم العثور على سجل الخطأ."}), 404


# ==============================================================================
# YouTube OAuth 2.0 Integration Handlers
# ==============================================================================

@dashboard_bp.route("/oauth/start")
@login_required
def oauth_start() -> Response:
    """Initiate YouTube OAuth 2.0 authentication flow."""
    client_id = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    redirect_uri = os.environ.get("YOUTUBE_REDIRECT_URI", "http://localhost:5000/oauth/callback").strip()

    if not client_id or not client_secret:
        flash(
            "بيانات اعتماد YouTube OAuth (CLIENT_ID / CLIENT_SECRET) غير مكتملة في ملف .env. "
            "يرجى ضبطها أولاً من إعدادات الأمان.",
            "warning",
        )
        return redirect(url_for("dashboard.security"))

    try:
        from google_auth_oauthlib.flow import Flow
        client_config = {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        }
        scopes = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/youtube.force-ssl",
        ]
        flow = Flow.from_client_config(client_config, scopes=scopes, redirect_uri=redirect_uri)
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        session["oauth_state"] = state
        return redirect(auth_url)
    except Exception as exc:
        flash(f"حدث خطأ أثناء بدء مصادقة يوتيوب: {str(exc)}", "danger")
        return redirect(url_for("dashboard.security"))


@dashboard_bp.route("/oauth/callback")
def oauth_callback() -> Response:
    """Handle callback from Google OAuth service."""
    state = session.get("oauth_state")
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        flash(f"تم رفض مصادقة يوتيوب من قبل المستخدم: {error}", "danger")
        return redirect(url_for("dashboard.security"))

    if not code:
        flash("رمز المصادقة غير موجود في رد جوجل.", "danger")
        return redirect(url_for("dashboard.security"))

    client_id = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    redirect_uri = os.environ.get("YOUTUBE_REDIRECT_URI", "http://localhost:5000/oauth/callback").strip()

    try:
        from google_auth_oauthlib.flow import Flow
        client_config = {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        }
        scopes = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/youtube.force-ssl",
        ]
        flow = Flow.from_client_config(client_config, scopes=scopes, state=state, redirect_uri=redirect_uri)
        flow.fetch_token(code=code)
        credentials = flow.credentials

        # Save credentials safely
        token_info = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
        }
        token_file = Path(__file__).resolve().parent.parent.parent / "config" / "youtube_token.json"
        token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(token_file, "w", encoding="utf-8") as f:
            json.dump(token_info, f, indent=2)

        add_audit_log(
            event_type="youtube_oauth_connected",
            ip_address=get_client_ip(),
            user_agent=request.user_agent.string,
            details="تم ربط حساب وقناة يوتيوب بنجاح عبر OAuth 2.0",
        )
        flash("تهانينا! تم ربط حساب وقناة يوتيوب بنجاح وجاهزة للرفع التلقائي.", "success")
    except Exception as exc:
        flash(f"فشل في استبدال رمز مصادقة يوتيوب: {str(exc)}", "danger")

    return redirect(url_for("dashboard.security"))
