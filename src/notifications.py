"""Notification management module for YouTube AI Autopilot.

Dispatches email alerts (SMTP) for production status, quota alerts, critical system
failures, and analytics summaries, with rate limiting (max 10 emails/hour) and
Arabic/English responsive HTML/plain-text templates.
"""

from __future__ import annotations

import html
import logging
import smtplib
import threading
import time
from collections import deque
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Union

from src.config import current_config

logger = logging.getLogger(__name__)


class NotificationType:
    """Supported notification category constants."""

    DAILY_REPORT = "daily_report"
    CRITICAL_ERROR = "critical_error"
    QUOTA_WARNING = "quota_warning"
    AUTH_EXPIRED = "auth_expired"
    EMERGENCY_STOP = "emergency_stop"
    WEEKLY_ANALYTICS = "weekly_analytics"


class NotificationManager:
    """Production notification coordinator with SMTP dispatch and rate limiting.

    Gracefully falls back to structured logging when SMTP credentials are not
    supplied or when hourly rate limits are saturated.
    """

    MAX_EMAILS_PER_HOUR = 10
    HOUR_IN_SECONDS = 3600.0

    # Arabic category titles and badge colors
    NOTIFICATION_METADATA: Dict[str, Dict[str, str]] = {
        NotificationType.DAILY_REPORT: {
            "title_ar": "تقرير الإنتاج اليومي - YouTube AI Autopilot",
            "title_en": "Daily Production Report",
            "color": "#2563eb",  # Blue
            "badge_ar": "تقرير يومي",
        },
        NotificationType.CRITICAL_ERROR: {
            "title_ar": "تنبيه: خطأ حرج في نظام الإنتاج الآلي",
            "title_en": "Critical System Error Alert",
            "color": "#dc2626",  # Red
            "badge_ar": "خطأ حرج",
        },
        NotificationType.QUOTA_WARNING: {
            "title_ar": "تحذير: استهلاك حصة API تجاوز 80%",
            "title_en": "API Quota Limit Warning",
            "color": "#d97706",  # Amber
            "badge_ar": "تحذير حصة",
        },
        NotificationType.AUTH_EXPIRED: {
            "title_ar": "تنبيه: انتهاء صلاحية مصادقة حساب YouTube",
            "title_en": "YouTube OAuth Authentication Expired",
            "color": "#ea580c",  # Orange
            "badge_ar": "مصادقة منتهية",
        },
        NotificationType.EMERGENCY_STOP: {
            "title_ar": "تحذير عاجل: تم تفعيل زر الإيقاف الطارئ (Emergency Stop)",
            "title_en": "Emergency Stop Activated",
            "color": "#b91c1c",  # Dark Red
            "badge_ar": "إيقاف طارئ",
        },
        NotificationType.WEEKLY_ANALYTICS: {
            "title_ar": "ملخص التحليلات الأسبوعي للقناة",
            "title_en": "Weekly Channel Analytics Summary",
            "color": "#16a34a",  # Green
            "badge_ar": "تحليلات أسبوعية",
        },
    }

    def __init__(self) -> None:
        """Initialize notification manager with rate limiting tracking."""
        self._sent_timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def is_configured(self) -> bool:
        """Check if SMTP credentials and target email are configured in settings.

        Returns:
            bool: True if SMTP server and recipient address exist.
        """
        return bool(
            current_config.SMTP_SERVER
            and current_config.NOTIFICATION_EMAIL
        )

    def _check_rate_limit(self) -> bool:
        """Verify if another email can be sent within the sliding hourly quota.

        Returns:
            bool: True if under limit, False if rate-limited.
        """
        now = time.time()
        with self._lock:
            # Drop entries older than 1 hour
            while self._sent_timestamps and now - self._sent_timestamps[0] > self.HOUR_IN_SECONDS:
                self._sent_timestamps.popleft()

            if len(self._sent_timestamps) >= self.MAX_EMAILS_PER_HOUR:
                logger.warning(
                    "Email notification suppressed: Rate limit exceeded (%d emails in last hour).",
                    len(self._sent_timestamps),
                )
                return False

            return True

    def _record_email_sent(self) -> None:
        """Record the current timestamp for hourly rate limiting."""
        with self._lock:
            self._sent_timestamps.append(time.time())

    def _render_html_template(
        self,
        notif_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Render a modern responsive HTML email body with Arabic RTL layout.

        Args:
            notif_type: Category identifier from NotificationType.
            message: Main notification message body.
            details: Optional dictionary of key-value metrics.

        Returns:
            str: Full HTML document string.
        """
        meta = self.NOTIFICATION_METADATA.get(
            notif_type,
            {
                "title_ar": "إشعار من YouTube AI Autopilot",
                "title_en": "Notification",
                "color": "#4b5563",
                "badge_ar": "إشعار عام",
            },
        )
        accent_color = meta["color"]
        badge_ar = meta["badge_ar"]
        title_ar = meta["title_ar"]

        details_rows = ""
        if details:
            for k, v in details.items():
                safe_k = html.escape(str(k))
                safe_v = html.escape(str(v))
                details_rows += f"""
                <tr>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb; font-weight: 600; color: #374151;">{safe_k}</td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb; color: #1f2937; direction: ltr; text-align: left;">{safe_v}</td>
                </tr>
                """

        details_table = ""
        if details_rows:
            details_table = f"""
            <div style="margin-top: 24px;">
                <h3 style="color: #1f2937; font-size: 16px; margin-bottom: 8px;">التفاصيل والبيانات الإحصائية:</h3>
                <table style="width: 100%; border-collapse: collapse; background: #f9fafb; border-radius: 8px; overflow: hidden; border: 1px solid #e5e7eb;">
                    {details_rows}
                </table>
            </div>
            """

        formatted_message = html.escape(message).replace("\n", "<br/>")

        html_content = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
    <title>{title_ar}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; color: #111827;">
    <div style="max-width: 600px; margin: 24px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); border: 1px solid #e5e7eb;">
        <!-- Header -->
        <div style="background-color: {accent_color}; padding: 24px; color: #ffffff; text-align: right;">
            <div style="display: inline-block; background: rgba(255, 255, 255, 0.2); padding: 4px 12px; border-radius: 9999px; font-size: 13px; font-weight: bold; margin-bottom: 8px;">
                {badge_ar}
            </div>
            <h1 style="margin: 0; font-size: 22px; font-weight: 700;">{title_ar}</h1>
            <p style="margin: 4px 0 0; font-size: 14px; opacity: 0.9;">YouTube AI Autopilot System</p>
        </div>

        <!-- Body -->
        <div style="padding: 24px; text-align: right; line-height: 1.6;">
            <div style="font-size: 16px; color: #374151;">
                {formatted_message}
            </div>

            {details_table}

            <div style="margin-top: 32px; padding-top: 16px; border-top: 1px solid #e5e7eb; font-size: 13px; color: #6b7280; text-align: center;">
                تم إنشاء هذا الإشعار تلقائياً بواسطة نظام YouTube AI Autopilot.<br/>
                التوقيت: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}
            </div>
        </div>
    </div>
</body>
</html>
"""
        return html_content

    def _render_plain_template(
        self,
        notif_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Render plain text fallback version for email clients.

        Args:
            notif_type: Notification type identifier.
            message: Main notification text.
            details: Optional dictionary of key-value metrics.

        Returns:
            str: Plain text email body.
        """
        meta = self.NOTIFICATION_METADATA.get(notif_type, {"title_ar": "إشعار نظام", "badge_ar": "إشعار"})
        lines: List[str] = [
            f"=== {meta['title_ar']} ===",
            f"التصنيف: {meta['badge_ar']}",
            "",
            message,
            "",
        ]
        if details:
            lines.append("--- التفاصيل الإضافية ---")
            for k, v in details.items():
                lines.append(f"{k}: {v}")
            lines.append("")

        lines.append(f"توقيت الإرسال: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
        lines.append("YouTube AI Autopilot System")
        return "\n".join(lines)

    def send_notification(
        self,
        notif_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send an email notification if SMTP is configured, respecting rate limits.

        If SMTP is unconfigured or rate limit is hit, logs details locally and returns.

        Args:
            notif_type: One of NotificationType constants.
            message: Notification message text.
            details: Optional dictionary with extra diagnostic metadata.

        Returns:
            bool: True if email dispatched successfully, False otherwise.
        """
        meta = self.NOTIFICATION_METADATA.get(notif_type, {"title_ar": "YouTube AI Autopilot Alert"})
        subject = meta["title_ar"]

        # Check configuration
        if not self.is_configured():
            logger.info(
                "SMTP not configured. Notification [%s] logged: %s (Details: %s)",
                notif_type,
                message,
                details,
            )
            return False

        # Check rate limit
        if not self._check_rate_limit():
            logger.warning(
                "Rate limit active. Notification [%s] suppressed: %s",
                notif_type,
                message,
            )
            return False

        # Construct email message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(subject, "utf-8").encode()
        msg["From"] = current_config.SMTP_USERNAME or current_config.NOTIFICATION_EMAIL
        msg["To"] = current_config.NOTIFICATION_EMAIL

        plain_text = self._render_plain_template(notif_type, message, details)
        html_text = self._render_html_template(notif_type, message, details)

        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_text, "html", "utf-8"))

        # Dispatch via SMTP
        try:
            server = smtplib.SMTP(current_config.SMTP_SERVER, current_config.SMTP_PORT, timeout=20)
            server.ehlo()
            if current_config.SMTP_PORT == 587:
                server.starttls()
                server.ehlo()

            if current_config.SMTP_USERNAME and current_config.SMTP_PASSWORD:
                server.login(current_config.SMTP_USERNAME, current_config.SMTP_PASSWORD)

            server.sendmail(
                msg["From"],
                [current_config.NOTIFICATION_EMAIL],
                msg.as_string(),
            )
            server.quit()

            self._record_email_sent()
            logger.info("Notification email dispatched successfully [%s] to %s", notif_type, current_config.NOTIFICATION_EMAIL)
            return True
        except Exception as exc:
            logger.error("Failed to deliver notification email [%s]: %s", notif_type, exc)
            return False

    def send_daily_report(self, report: Any) -> bool:
        """Send structured daily production summary notification.

        Args:
            report: ProductionReport instance or dictionary.

        Returns:
            bool: True if notification was processed.
        """
        # Extract fields from report object or dictionary
        if hasattr(report, "to_dict"):
            data = report.to_dict()
        elif isinstance(report, dict):
            data = report
        else:
            data = {"report": str(report)}

        total_slots = data.get("total_slots", 0)
        successful = data.get("successful", 0)
        failed = data.get("failed", 0)
        skipped = data.get("skipped", 0)
        date_str = data.get("date", time.strftime("%Y-%m-%d"))

        msg = (
            f"اكتملت دورة الإنتاج اليومية بنجاح لتاريخ {date_str}.\n"
            f"تمت معالجة {successful} فيديو من أصل {total_slots} موعد مجدول."
        )

        details = {
            "تاريخ الإنتاج": date_str,
            "إجمالي المواعيد المجدولة": total_slots,
            "فيديوهات تم إنتاجها بنجاح": successful,
            "فيديوهات فشل إنتاجها": failed,
            "مواعيد تم تخطيها": skipped,
        }

        if "total_duration_sec" in data:
            details["إجمالي زمن الإنتاج"] = f"{data['total_duration_sec']:.1f} ثانية"

        if "quota_used" in data:
            details["استهلاك حصة YouTube"] = f"{data['quota_used']} وحدة"

        return self.send_notification(
            notif_type=NotificationType.DAILY_REPORT,
            message=msg,
            details=details,
        )

    def send_critical_error(self, module: str, error_message: str, details: Optional[Dict[str, Any]] = None) -> bool:
        """Send critical error alert.

        Args:
            module: Source module name where failure occurred.
            error_message: Primary exception or error summary.
            details: Optional stack traces or state parameters.

        Returns:
            bool: True if delivered.
        """
        msg = f"حدث خطأ حرج غير متوقع في الوحدة [{module}]:\n{error_message}"
        merged_details = {"الوحدة المصدر": module, "الخطأ": error_message}
        if details:
            merged_details.update(details)

        return self.send_notification(
            notif_type=NotificationType.CRITICAL_ERROR,
            message=msg,
            details=merged_details,
        )

    def send_quota_warning(self, api: str, usage_pct: float, remaining: int) -> bool:
        """Send quota consumption warning.

        Args:
            api: API name.
            usage_pct: Percentage of daily quota consumed.
            remaining: Units left.

        Returns:
            bool: True if delivered.
        """
        msg = (
            f"تحذير: استهلاك حصة {api.upper()} تجاوز {usage_pct:.1f}%!\n"
            f"الوحدات المتبقية لليوم: {remaining} وحدة."
        )
        return self.send_notification(
            notif_type=NotificationType.QUOTA_WARNING,
            message=msg,
            details={"اسم الواجهة (API)": api, "نسبة الاستهلاك": f"{usage_pct:.1f}%", "الوحدات المتبقية": remaining},
        )

    def send_auth_expired(self, service: str = "YouTube OAuth") -> bool:
        """Send expired authorization warning.

        Args:
            service: Service name requiring re-authentication.

        Returns:
            bool: True if delivered.
        """
        msg = (
            f"انتهت صلاحية رمز المصادقة (Refresh Token) لخدمة {service}.\n"
            "يرجى تسجيل الدخول إلى لوحة التحكم وإعادة ربط الحساب فوراً لاستئناف النشر التلقائي."
        )
        return self.send_notification(
            notif_type=NotificationType.AUTH_EXPIRED,
            message=msg,
            details={"الخدمة": service, "الإجراء المطلوب": "إعادة تسجيل الدخول عبر /oauth/login"},
        )

    def send_emergency_stop_alert(self, reason: str = "Manual trigger or safety limit") -> bool:
        """Send emergency stop alert.

        Args:
            reason: Trigger rationale.

        Returns:
            bool: True if delivered.
        """
        msg = (
            "تم تفعيل وضع الإيقاف الطارئ (Emergency Stop)!\n"
            "تم إيقاف جميع مهام الإنتاج والنشر التلقائي على الفور لحماية القناة."
        )
        return self.send_notification(
            notif_type=NotificationType.EMERGENCY_STOP,
            message=msg,
            details={"السبب": reason, "الحالة": "النظام متوقف مؤقتاً"},
        )
