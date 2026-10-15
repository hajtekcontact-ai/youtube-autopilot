"""System Health and Readiness Verification Module.

Monitors system health across all mission-critical components: SQLite database,
FFmpeg media processing suite, storage disk space, external API credentials
(YouTube OAuth, Gemini API), and YouTube Data API quota remaining. Produces
bilingual reports with Arabic dashboard status strings and alert thresholds.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.getenv("DATABASE_URL", "data/autopilot.db").replace("sqlite:///", "")

# Alert thresholds
DISK_CRITICAL_GB = 2.0
DISK_WARNING_GB = 5.0
DISK_WARNING_PCT = 10.0

QUOTA_CRITICAL_REMAINING = 500
QUOTA_WARNING_REMAINING = 2000
DEFAULT_DAILY_QUOTA_LIMIT = 10000

MAX_CONSECUTIVE_ERRORS_THRESHOLD = 5

_PROCESS_START_TIME = time.time()


@dataclass
class ComponentStatus:
    """Represents the operational health status of an individual system component.

    Attributes:
        component: Identifier name of the component (e.g., 'database', 'ffmpeg').
        status: Status classification ('healthy', 'degraded', 'unhealthy').
        message_en: Technical status description in English.
        message_ar: Dashboard UI status description in Arabic.
        details: Diagnostic dictionary containing metric details.
        checked_at: ISO timestamp when check was executed.
    """

    component: str
    status: str
    message_en: str
    message_ar: str
    details: Dict[str, Any] = field(default_factory=dict)
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert component status to dictionary."""
        return asdict(self)


@dataclass
class HealthReport:
    """Comprehensive system-wide health evaluation report.

    Attributes:
        overall_status: Aggregated state ('healthy', 'degraded', 'unhealthy').
        uptime_seconds: System operational uptime in seconds.
        components: Map of component names to ComponentStatus instances.
        last_successful_run: ISO timestamp of last successful production/publish cycle.
        alerts: List of technical English warning/critical alert strings.
        alerts_ar: List of Arabic alert strings for dashboard banners.
        summary_ar: Arabic dashboard summary message.
        summary_en: Technical English summary message.
        timestamp: ISO timestamp of report generation.
    """

    overall_status: str
    uptime_seconds: float
    components: Dict[str, ComponentStatus]
    last_successful_run: Optional[str]
    alerts: List[str] = field(default_factory=list)
    alerts_ar: List[str] = field(default_factory=list)
    summary_ar: str = ""
    summary_en: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert health report to dictionary."""
        d = asdict(self)
        d["components"] = {k: v.to_dict() for k, v in self.components.items()}
        return d


class HealthCheck:
    """Verifies operational readiness and ongoing health across system components.

    Monitors database integrity, video transcoding tools, available disk space,
    authentication credentials, and daily quota consumption.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        daily_quota_limit: int = DEFAULT_DAILY_QUOTA_LIMIT,
        storage_check_path: Optional[str] = None,
    ) -> None:
        """Initialize HealthCheck engine.

        Args:
            db_path: Path to SQLite database file.
            daily_quota_limit: Total daily quota allocation for YouTube Data API.
            storage_check_path: Filesystem path to monitor for available disk space.
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        self.daily_quota_limit = daily_quota_limit
        self.storage_check_path = storage_check_path or os.path.dirname(os.path.abspath(self.db_path))
        self._init_health_tables()

    def _get_connection(self) -> sqlite3.Connection:
        """Obtain thread-safe connection to database."""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_health_tables(self) -> None:
        """Create system health tracking tables in SQLite."""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS system_health_state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                    )
                # Seed system start time if not already recorded
                conn.execute(
                    """
                    INSERT OR IGNORE INTO system_health_state (key, value)
                    VALUES ('system_start_time', ?);
                    """,
                    (datetime.now(timezone.utc).isoformat(),),
                )
                conn.commit()
        except sqlite3.Error as exc:
            logger.error("Failed to initialize health tables: %s", exc)

    def record_successful_run(self) -> None:
        """Record the timestamp of a successful production or publication cycle."""
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO system_health_state (key, value, updated_at)
                    VALUES ('last_successful_run', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
                    """,
                    (now_iso, now_iso),
                )
                # Reset consecutive error counter on success
                conn.execute(
                    """
                    INSERT INTO system_health_state (key, value, updated_at)
                    VALUES ('consecutive_errors', '0', ?)
                    ON CONFLICT(key) DO UPDATE SET value = '0', updated_at = excluded.updated_at;
                    """,
                    (now_iso,),
                )
                conn.commit()
        except sqlite3.Error as exc:
            logger.error("Failed recording successful run: %s", exc)

    def record_error(self, error_message: str) -> None:
        """Increment consecutive error count and log error details."""
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT value FROM system_health_state WHERE key = 'consecutive_errors'")
                row = cur.fetchone()
                current_errs = int(row["value"]) if row else 0
                new_errs = current_errs + 1

                conn.execute(
                    """
                    INSERT INTO system_health_state (key, value, updated_at)
                    VALUES ('consecutive_errors', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
                    """,
                    (str(new_errs), now_iso),
                )
                conn.execute(
                    """
                    INSERT INTO system_health_state (key, value, updated_at)
                    VALUES ('last_error_message', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
                    """,
                    (error_message[:500], now_iso),
                )
                conn.commit()
        except sqlite3.Error as exc:
            logger.error("Failed recording error state: %s", exc)

    def get_last_successful_run(self) -> Optional[str]:
        """Retrieve ISO timestamp of the last successful production run."""
        try:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT value FROM system_health_state WHERE key = 'last_successful_run'")
                row = cur.fetchone()
                return row["value"] if row else None
        except sqlite3.Error:
            return None

    def get_uptime_seconds(self) -> float:
        """Compute system uptime in seconds from process start time."""
        return max(0.0, time.time() - _PROCESS_START_TIME)

    def check_database(self) -> ComponentStatus:
        """Verify SQLite database connectivity, read/write ability, and latency."""
        t0 = time.time()
        try:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT 1 AS alive;")
                val = cur.fetchone()["alive"]
                if val != 1:
                    raise sqlite3.OperationalError("Invalid response from database query.")
            latency_ms = round((time.time() - t0) * 1000.0, 2)

            if latency_ms > 1000.0:
                return ComponentStatus(
                    component="database",
                    status="degraded",
                    message_en=f"Database connected but high latency ({latency_ms} ms).",
                    message_ar=f"قاعدة البيانات متصلة ولكن بزمن استجابة مرتفع ({latency_ms} مللي ثانية).",
                    details={"latency_ms": latency_ms, "db_path": self.db_path},
                )

            return ComponentStatus(
                component="database",
                status="healthy",
                message_en=f"Database is healthy and responsive (latency: {latency_ms} ms).",
                message_ar="قاعدة البيانات متصلة وتعمل بكفاءة تامة واستجابة سريعة.",
                details={"latency_ms": latency_ms, "db_path": self.db_path},
            )
        except Exception as exc:
            logger.error("Database health check failed: %s", exc)
            return ComponentStatus(
                component="database",
                status="unhealthy",
                message_en=f"Database connection error: {exc}",
                message_ar="فشل حرج في الاتصال بقاعدة البيانات المحلية. تعذر فتح الملف أو قراءة الجداول.",
                details={"error": str(exc), "db_path": self.db_path},
            )

    def check_ffmpeg(self) -> ComponentStatus:
        """Verify presence and executable functionality of FFmpeg and FFprobe."""
        ffmpeg_bin = shutil.which("ffmpeg")
        ffprobe_bin = shutil.which("ffprobe")

        if not ffmpeg_bin:
            return ComponentStatus(
                component="ffmpeg",
                status="unhealthy",
                message_en="FFmpeg binary not found in system PATH.",
                message_ar="محرك معالجة الفيديو FFmpeg غير مثبت أو غير متاح في مسار النظام ($PATH). لن يتم توليد الفيديوهات.",
                details={"ffmpeg_found": False, "ffprobe_found": bool(ffprobe_bin)},
            )

        try:
            res = subprocess.run(
                [ffmpeg_bin, "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
            )
            first_line = res.stdout.splitlines()[0] if res.stdout else "FFmpeg installed"
            version_str = first_line.split("Copyright")[0].strip()

            return ComponentStatus(
                component="ffmpeg",
                status="healthy",
                message_en=f"FFmpeg is available: {version_str}.",
                message_ar=f"محرك الفيديو FFmpeg متاح وجاهز للإنتاج ({version_str}).",
                details={
                    "ffmpeg_path": ffmpeg_bin,
                    "ffprobe_path": ffprobe_bin,
                    "version": version_str,
                },
            )
        except Exception as exc:
            logger.warning("Error querying FFmpeg version: %s", exc)
            return ComponentStatus(
                component="ffmpeg",
                status="degraded",
                message_en=f"FFmpeg executable exists at {ffmpeg_bin} but returned error during execution.",
                message_ar=f"برنامج FFmpeg موجود ولكن حدث خطأ أثناء تشغيله: {exc}",
                details={"ffmpeg_path": ffmpeg_bin, "error": str(exc)},
            )

    def check_disk_space(self) -> ComponentStatus:
        """Verify available storage space against warning and critical thresholds."""
        try:
            check_dir = self.storage_check_path
            if not os.path.exists(check_dir):
                check_dir = "."

            total, used, free = shutil.disk_usage(check_dir)
            total_gb = round(total / (1024**3), 2)
            used_gb = round(used / (1024**3), 2)
            free_gb = round(free / (1024**3), 2)
            free_pct = round((free / total) * 100.0, 1)

            details = {
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": free_gb,
                "free_percentage": free_pct,
                "monitored_path": check_dir,
            }

            if free_gb < DISK_CRITICAL_GB or free_pct < 5.0:
                return ComponentStatus(
                    component="disk_space",
                    status="unhealthy",
                    message_en=f"CRITICAL: Free disk space dangerously low ({free_gb} GB, {free_pct}%).",
                    message_ar=f"تحذير حرج: مساحة القرص المتبقية منخفضة للغاية ({free_gb} جيجابايت، {free_pct}%). خطر توقف معالجة الفيديو.",
                    details=details,
                )
            elif free_gb < DISK_WARNING_GB or free_pct < DISK_WARNING_PCT:
                return ComponentStatus(
                    component="disk_space",
                    status="degraded",
                    message_en=f"WARNING: Free disk space approaching limit ({free_gb} GB, {free_pct}%).",
                    message_ar=f"تنبيه: مساحة التخزين المتوفرة تقترب من الحد الأدنى ({free_gb} جيجابايت، {free_pct}%).",
                    details=details,
                )

            return ComponentStatus(
                component="disk_space",
                status="healthy",
                message_en=f"Disk space is ample ({free_gb} GB available, {free_pct}% free).",
                message_ar=f"مساحة التخزين كافية وجاهزة ({free_gb} جيجابايت متوفرة).",
                details=details,
            )
        except Exception as exc:
            logger.error("Failed checking disk space: %s", exc)
            return ComponentStatus(
                component="disk_space",
                status="degraded",
                message_en=f"Failed checking disk usage: {exc}",
                message_ar=f"تعذر التحقق من مساحة القرص التخزيني: {exc}",
                details={"error": str(exc)},
            )

    def check_api_credentials(self) -> ComponentStatus:
        """Verify presence of YouTube OAuth tokens, client secrets, and AI API keys."""
        yt_client_id = bool(os.getenv("YOUTUBE_CLIENT_ID"))
        yt_client_secret = bool(os.getenv("YOUTUBE_CLIENT_SECRET"))
        yt_refresh_token = bool(os.getenv("YOUTUBE_REFRESH_TOKEN"))
        token_path = os.getenv("YOUTUBE_TOKEN_PATH", "token.json")
        has_token_file = os.path.exists(token_path)

        gemini_key = bool(os.getenv("GEMINI_API_KEY"))

        has_youtube_creds = has_token_file or (yt_client_id and yt_client_secret and yt_refresh_token)

        details = {
            "youtube_token_file_exists": has_token_file,
            "youtube_oauth_env_configured": bool(yt_client_id and yt_client_secret),
            "gemini_api_key_configured": gemini_key,
        }

        if has_youtube_creds and gemini_key:
            return ComponentStatus(
                component="api_credentials",
                status="healthy",
                message_en="All required API credentials (YouTube OAuth & Gemini) are fully configured.",
                message_ar="جميع بيانات الاعتماد ومفاتيح الربط (YouTube OAuth و Gemini) مهيأة بنجاح.",
                details=details,
            )
        elif has_youtube_creds and not gemini_key:
            return ComponentStatus(
                component="api_credentials",
                status="degraded",
                message_en="YouTube credentials present, but GEMINI_API_KEY is not configured.",
                message_ar="بيانات اعتماد YouTube متصلة، ولكن مفتاح Gemini غير محدد (سيتم استخدام القوالب الاحتياطية).",
                details=details,
            )
        else:
            return ComponentStatus(
                component="api_credentials",
                status="unhealthy",
                message_en="Missing YouTube credentials (neither token.json nor OAuth env vars found).",
                message_ar="بيانات اعتماد YouTube مفقودة (لم يتم العثور على token.json أو متغيرات البيئة). لن يتمكن النظام من النشر.",
                details=details,
            )

    def check_quota(self) -> ComponentStatus:
        """Verify YouTube Data API quota consumption for the current day."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        units_used = 0
        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "SELECT units_used FROM api_quota_tracker WHERE date_str = ? AND api_name = 'youtube_data'",
                    (today,),
                )
                row = cur.fetchone()
                if row:
                    units_used = int(row["units_used"])
        except Exception as exc:
            logger.debug("Quota query exception (may be uninitialized yet): %s", exc)

        remaining = max(0, self.daily_quota_limit - units_used)
        used_pct = round((units_used / max(1, self.daily_quota_limit)) * 100.0, 1)

        details = {
            "date": today,
            "units_used": units_used,
            "daily_limit": self.daily_quota_limit,
            "remaining_units": remaining,
            "used_percentage": used_pct,
        }

        if remaining <= QUOTA_CRITICAL_REMAINING:
            return ComponentStatus(
                component="quota",
                status="unhealthy",
                message_en=f"CRITICAL: YouTube API quota near exhaustion ({remaining} units remaining).",
                message_ar=f"تحذير حرج: استنفاد الحصة اليومية لـ YouTube Data API (متبقي {remaining} وحدة من {self.daily_quota_limit}).",
                details=details,
            )
        elif remaining <= QUOTA_WARNING_REMAINING:
            return ComponentStatus(
                component="quota",
                status="degraded",
                message_en=f"WARNING: YouTube API quota is running low ({remaining} units remaining).",
                message_ar=f"تنبيه: الحصة اليومية المتبقية لـ YouTube API منخفضة (متبقي {remaining} وحدة، تم استهلاك {used_pct}%).",
                details=details,
            )

        return ComponentStatus(
            component="quota",
            status="healthy",
            message_en=f"YouTube API quota healthy ({remaining}/{self.daily_quota_limit} units remaining).",
            message_ar=f"الحصة اليومية لـ YouTube API مستقرة (متبقي {remaining:,} وحدة من أصل {self.daily_quota_limit:,}).",
            details=details,
        )

    def check_recent_errors(self) -> Tuple[int, Optional[str]]:
        """Query consecutive error count and message from state table."""
        try:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT key, value FROM system_health_state WHERE key IN ('consecutive_errors', 'last_error_message');")
                state = {r["key"]: r["value"] for r in cur.fetchall()}
                consecutive = int(state.get("consecutive_errors", 0))
                msg = state.get("last_error_message")
                return consecutive, msg
        except Exception:
            return 0, None

    def get_component_status(self, component: str) -> ComponentStatus:
        """Check the status of a specific component by name.

        Args:
            component: Component key ('database', 'ffmpeg', 'disk_space', 'api_credentials', 'quota').

        Returns:
            ComponentStatus representing the component's state.
        """
        component_lower = component.lower()
        if component_lower in ("database", "db"):
            return self.check_database()
        elif component_lower in ("ffmpeg", "video_engine"):
            return self.check_ffmpeg()
        elif component_lower in ("disk_space", "storage", "disk"):
            return self.check_disk_space()
        elif component_lower in ("api_credentials", "credentials", "auth"):
            return self.check_api_credentials()
        elif component_lower in ("quota", "api_quota"):
            return self.check_quota()
        else:
            return ComponentStatus(
                component=component,
                status="unhealthy",
                message_en=f"Unknown component identifier '{component}'.",
                message_ar=f"مكون غير معروف في فحص النظام: '{component}'.",
                details={"requested_component": component},
            )

    def run_health_check(self) -> HealthReport:
        """Perform comprehensive health check across all system components.

        Evaluates individual statuses, analyzes error counters and thresholds,
        and constructs bilingual summary strings for dashboard monitoring.

        Returns:
            HealthReport containing complete diagnostics, uptime, and alerts.
        """
        components: Dict[str, ComponentStatus] = {
            "database": self.check_database(),
            "ffmpeg": self.check_ffmpeg(),
            "disk_space": self.check_disk_space(),
            "api_credentials": self.check_api_credentials(),
            "quota": self.check_quota(),
        }

        alerts: List[str] = []
        alerts_ar: List[str] = []

        unhealthy_count = sum(1 for c in components.values() if c.status == "unhealthy")
        degraded_count = sum(1 for c in components.values() if c.status == "degraded")

        for c in components.values():
            if c.status == "unhealthy":
                alerts.append(f"CRITICAL [{c.component}]: {c.message_en}")
                alerts_ar.append(f"خطر حرج [{c.component}]: {c.message_ar}")
            elif c.status == "degraded":
                alerts.append(f"WARNING [{c.component}]: {c.message_en}")
                alerts_ar.append(f"تنبيه تحذيري [{c.component}]: {c.message_ar}")

        # Check consecutive errors threshold
        consecutive_errors, last_err = self.check_recent_errors()
        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS_THRESHOLD:
            alerts.append(f"CRITICAL [pipeline]: {consecutive_errors} consecutive task failures. Last: {last_err}")
            alerts_ar.append(f"خطر حرج [الإنتاج]: تم تسجيل {consecutive_errors} أخطاء متتالية في الإنتاج. الخطأ الأخير: {last_err}")
            unhealthy_count += 1

        # Overall Status
        if unhealthy_count > 0:
            overall_status = "unhealthy"
            summary_en = f"System unhealthy: {unhealthy_count} critical failures detected."
            summary_ar = "حالة النظام: حرجة! تم رصد أعطال رئيسية تتطلب تدخلاً فورياً لاستمرار العمليات."
        elif degraded_count > 0:
            overall_status = "degraded"
            summary_en = f"System degraded: {degraded_count} warnings detected."
            summary_ar = "حالة النظام: تحذيرية؛ النظام يعمل جزئياً مع وجود تنبيهات تحتاج إلى مراجعة."
        else:
            overall_status = "healthy"
            summary_en = "All system components operational and healthy."
            summary_ar = "حالة النظام: ممتازة ومستقرة؛ جميع المكونات تعمل بكفاءة تامة."

        uptime_sec = self.get_uptime_seconds()
        last_success = self.get_last_successful_run()

        logger.info("Health check completed: overall_status=%s, alerts=%d", overall_status, len(alerts))

        return HealthReport(
            overall_status=overall_status,
            uptime_seconds=round(uptime_sec, 1),
            components=components,
            last_successful_run=last_success,
            alerts=alerts,
            alerts_ar=alerts_ar,
            summary_ar=summary_ar,
            summary_en=summary_en,
        )
