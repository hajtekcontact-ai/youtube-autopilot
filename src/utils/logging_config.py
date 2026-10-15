"""Logging configuration module for YouTube AI Autopilot.

Provides JSON structured logging, log rotation (10MB, 5 backups), sensitive data
filtering (API keys, tokens, passwords), Arabic event translations for the dashboard,
and integration with the SQLite SystemLog database table.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

# Arabic translation dictionary for key system and dashboard operational messages
ARABIC_EVENT_MESSAGES: Dict[str, str] = {
    "system_started": "تم بدء تشغيل نظام النشر الآلي بنجاح",
    "system_stopped": "تم إيقاف تشغيل نظام النشر الآلي",
    "emergency_stop_activated": "تحذير: تم تفعيل الإيقاف الطارئ لجميع العمليات",
    "emergency_stop_deactivated": "تم إلغاء تفعيل الإيقاف الطارئ واستئناف العمل",
    "cost_kill_switch_activated": "تحذير: تم تفعيل مفتاح إيقاف التكاليف لتجاوز الحد المسموح",
    "cost_kill_switch_deactivated": "تم إلغاء تفعيل قاطع التكاليف",
    "database_initialized": "تم تهيئة قاعدة البيانات وتحديث الجداول بنجاح",
    "video_generation_started": "بدأ إنتاج فيديو جديد",
    "video_generation_completed": "تم الانتهاء من إنتاج الفيديو بنجاح",
    "video_generation_failed": "فشل في إنتاج الفيديو",
    "video_upload_started": "بدأ رفع الفيديو إلى يوتيوب",
    "video_published": "تم نشر الفيديو بنجاح على قناة يوتيوب",
    "video_upload_failed": "فشل في رفع الفيديو إلى يوتيوب",
    "quota_exceeded": "تم تجاوز الحصة اليومية لواجهة يوتيوب (API Quota)",
    "token_refreshed": "تم تجديد رمز الوصول (OAuth Token) بنجاح",
    "token_refresh_failed": "فشل تجديد رمز وصول يوتيوب",
    "analytics_snapshot_saved": "تم حفظ لقطة الإحصائيات الدورية للفيديو",
    "scheduler_tick": "تحقق المجدول الدوري من المواعيد",
    "schedule_slot_assigned": "تم تعيين فيديو للخانة الزمنية المجدولة",
}


def get_arabic_message(event_key: str, default: Optional[str] = None) -> str:
    """Retrieve an Arabic UI message for a system event key.

    Args:
        event_key: Event identifier string.
        default: Fallback text if event_key is not found.

    Returns:
        str: Arabic descriptive message or fallback.
    """
    return ARABIC_EVENT_MESSAGES.get(event_key, default or event_key)


class SensitiveDataFilter(logging.Filter):
    """Filter to redact sensitive tokens, passwords, and API keys from log messages."""

    # Patterns for secrets, tokens, API keys, and Authorization headers
    SENSITIVE_PATTERNS = [
        re.compile(r"AIza[0-9A-Za-z_\-]{30,45}"),  # Google / Gemini API keys
        re.compile(r"Bearer\s+([a-zA-Z0-9_\-\.]{15,})", re.IGNORECASE),
        re.compile(r"(?i)\b(api[_-]?key|key|password|secret|token|client_secret|encryption_key|credential)\b\s*[:=]\s*['\"]?([^\s,'\"&]{4,})['\"]?"),
        re.compile(r"(ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{40,})"),  # GitHub Tokens
        re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----.*?-----END [A-Z ]+ PRIVATE KEY-----", re.DOTALL),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitize message and arguments in the log record.

        Args:
            record: Incoming log record.

        Returns:
            bool: Always True to continue propagation.
        """
        if isinstance(record.msg, str):
            record.msg = self.mask_text(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self.mask_text(str(v)) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(self.mask_text(str(a)) if isinstance(a, str) else a for a in record.args)

        return True

    @classmethod
    def mask_text(cls, text: str) -> str:
        """Apply regex masks to redact sensitive content.

        Args:
            text: Text to sanitize.

        Returns:
            str: Masked text with secrets replaced by [REDACTED].
        """
        if not text:
            return ""

        masked = text
        for pattern in cls.SENSITIVE_PATTERNS:
            def repl(m: re.Match[str]) -> str:
                # If matched key: secret group
                if len(m.groups()) >= 2:
                    return f"{m.group(1)}: [REDACTED]"
                return "[REDACTED]"
            masked = pattern.sub(repl, masked)

        return masked


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter for machine readability and dashboards."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record into a single-line JSON string.

        Args:
            record: LogRecord instance.

        Returns:
            str: JSON string.
        """
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "line": record.lineno,
            "message": SensitiveDataFilter.mask_text(record.getMessage()),
        }

        # Check for Arabic message or event key passed in extra
        event_key = getattr(record, "event_key", None)
        if event_key and event_key in ARABIC_EVENT_MESSAGES:
            log_entry["arabic_message"] = ARABIC_EVENT_MESSAGES[event_key]
        elif hasattr(record, "arabic_message"):
            log_entry["arabic_message"] = getattr(record, "arabic_message")

        # Capture extra structured details if present
        details = getattr(record, "details", None)
        if isinstance(details, dict):
            log_entry["details"] = details
        elif details is not None:
            log_entry["details"] = str(details)

        # Include exception tracebacks
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        elif record.stack_info:
            log_entry["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(log_entry, ensure_ascii=False)


class StandardFormatter(logging.Formatter):
    """Clean, human-readable console formatter with timestamp and log level."""

    FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self.FORMAT, datefmt="%Y-%m-%d %H:%M:%S")


class DatabaseLogHandler(logging.Handler):
    """Logging handler that persists log records directly into the SQLite SystemLog table.

    Operates safely with defensive error handling so database issues or locks
    never interrupt application execution.
    """

    def __init__(self, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._in_emit = False

    def emit(self, record: logging.LogRecord) -> None:
        """Write the log record to the system_logs table.

        Args:
            record: LogRecord to record.
        """
        # Guard against recursive logging calls
        if self._in_emit:
            return

        # Ignore internal SQLAlchemy logs to prevent loop
        if record.name.startswith("sqlalchemy"):
            return

        self._in_emit = True
        try:
            # Lazy import to avoid circular dependencies during initialization
            from src.database import SystemLog, get_session_factory

            session_factory = get_session_factory()
            details: Dict[str, Any] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }

            if hasattr(record, "details") and isinstance(record.details, dict):
                details.update(record.details)

            if record.exc_info:
                details["exception"] = traceback.format_exception(*record.exc_info)

            arabic_msg = getattr(record, "arabic_message", None)
            if arabic_msg:
                details["arabic_message"] = arabic_msg

            raw_msg = record.getMessage()
            masked_msg = SensitiveDataFilter.mask_text(raw_msg)[:5000]

            with session_factory() as session:
                log_row = SystemLog(
                    level=record.levelname,
                    module=record.name[:100],
                    message=masked_msg,
                    details=json.dumps(details, ensure_ascii=False),
                    created_at=datetime.utcnow(),
                )
                session.add(log_row)
                session.commit()
        except Exception:
            # Never let logging failure propagate to application logic
            pass
        finally:
            self._in_emit = False


def setup_logging(
    log_level: str = "INFO",
    log_dir: Optional[Union[str, Path]] = None,
    enable_db_logging: bool = True,
    enable_json_file: bool = True,
) -> logging.Logger:
    """Configure system-wide logging with console, rotating file, and database handlers.

    Features:
    - Rotating file handler (10MB max, 5 backups, UTF-8)
    - JSON structured log file (autopilot.json.log)
    - Console stream handler with clean formatting
    - Sensitive data masking filter applied to all handlers
    - Module-specific log levels for noisy external libraries
    - Optional SQLite SystemLog database persistence

    Args:
        log_level: Root logger level name (e.g. 'DEBUG', 'INFO', 'WARNING').
        log_dir: Directory where log files are stored. Defaults to data/logs.
        enable_db_logging: Whether to attach DatabaseLogHandler.
        enable_json_file: Whether to produce structured JSON log file.

    Returns:
        logging.Logger: Configured root logger.
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Clear existing handlers to prevent duplicate logging
    root_logger.handlers.clear()

    # Shared sensitive data filter
    sensitive_filter = SensitiveDataFilter()
    root_logger.addFilter(sensitive_filter)

    # 1. Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(StandardFormatter())
    console_handler.addFilter(sensitive_filter)
    root_logger.addHandler(console_handler)

    # Resolve Logs Directory
    if log_dir is None:
        target_log_dir = Path(__file__).resolve().parent.parent.parent / "data" / "logs"
    else:
        target_log_dir = Path(log_dir).resolve()
    target_log_dir.mkdir(parents=True, exist_ok=True)

    # 2. Rotating Text File Handler (10MB, 5 backups)
    text_log_file = target_log_dir / "autopilot.log"
    file_handler = RotatingFileHandler(
        filename=str(text_log_file),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(StandardFormatter())
    file_handler.addFilter(sensitive_filter)
    root_logger.addHandler(file_handler)

    # 3. Rotating JSON Structured File Handler (10MB, 5 backups)
    if enable_json_file:
        json_log_file = target_log_dir / "autopilot.json.log"
        json_handler = RotatingFileHandler(
            filename=str(json_log_file),
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        json_handler.setLevel(numeric_level)
        json_handler.setFormatter(JsonFormatter())
        json_handler.addFilter(sensitive_filter)
        root_logger.addHandler(json_handler)

    # 4. SQLite Database Log Handler
    if enable_db_logging:
        db_handler = DatabaseLogHandler(level=logging.INFO)
        db_handler.addFilter(sensitive_filter)
        root_logger.addHandler(db_handler)

    # Tune third-party library loggers
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    logging.getLogger("google.auth").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    root_logger.info("Logging configured successfully at level %s (dir: %s)", log_level, target_log_dir)
    return root_logger
